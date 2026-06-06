# Jenkins Integration Guide

The agentic-sdlc app provides comprehensive bi-directional integration with Jenkins, allowing:

1. **Jenkins → asdlc**: Trigger code analysis scans from Jenkins builds
2. **asdlc → Jenkins**: Report findings back to Jenkins via callbacks, JUnit XML, and API updates

## Architecture

```
┌─────────────┐          ┌──────────────┐          ┌──────────────────┐
│  Jenkins    │          │ asdlc Server │          │ Jenkins Webhook  │
│  Pipeline   │          │              │          │ Handler Job      │
└──────┬──────┘          └──────┬───────┘          └────────┬─────────┘
       │                        │                           │
       │ 1. POST /scan          │                           │
       │ + callback_url         │                           │
       ├───────────────────────>│                           │
       │                        │                           │
       │ 2. Scan in progress    │                           │
       │ (202 Accepted)         │                           │
       │<───────────────────────┤                           │
       │                        │                           │
       │                        │ 3. POST callback_url      │
       │                        │ + JUnit + JSON + API      │
       │                        ├──────────────────────────>│
       │                        │                           │
       │                        │    4. Publish reports     │
       │                        │    (JUnit, artifacts)     │
       │                        │<──────────────────────────┤
```

## Quick Start

### Prerequisites

1. asdlc server running (e.g., `http://localhost:8088`)
2. Jenkins with:
   - Generic Webhook Trigger plugin (or similar)
   - Pipeline/Groovy support
3. API credentials configured

### Step 1: Configure Jenkins Credentials

In Jenkins, add credentials for asdlc:

```
Manage Jenkins → Manage Credentials → System → Global credentials (unrestricted)
```

Add two secret text credentials:

1. **`asdlc-server-url`**: `http://localhost:8088`
2. **`asdlc-api-key`**: Your asdlc API key (from `.env` or `ASDLC_API_KEY`)
3. **`jenkins-api-token`**: Your Jenkins API token (User → Configure → API Token)

### Step 2: Install Shared Library (Optional but Recommended)

Copy `examples/asdlc-shared-library.groovy` to your Jenkins shared library:

```
Jenkins Home/
  libraries/
    asdlc-shared-library/
      vars/
        asdlcScan.groovy  (copy the shared library code here)
```

Or configure it as a shared library in Jenkins UI:

```
Manage Jenkins → Configure System → Global Pipeline Libraries
Name: asdlc-shared-library
Modern SCM: <point to your repo with examples/asdlc-shared-library.groovy>
```

### Step 3: Use in Your Jenkinsfile

Basic usage:

```groovy
@Library('asdlc-shared-library') _

pipeline {
    agent any
    
    environment {
        ASDLC_URL = credentials('asdlc-server-url')
        ASDLC_API_KEY = credentials('asdlc-api-key')
        JENKINS_API_TOKEN = credentials('jenkins-api-token')
    }
    
    stages {
        stage('asdlc Scan') {
            steps {
                script {
                    asdlcScan(
                        repo_path: "${WORKSPACE}",
                        branch: "${GIT_BRANCH}",
                        asdlc_url: "${ASDLC_URL}",
                        asdlc_api_key: "${ASDLC_API_KEY}",
                        jenkins_api_token: "${JENKINS_API_TOKEN}"
                    )
                }
            }
        }
    }
    
    post {
        always {
            // Reports are published automatically by webhook handler
            junit testResults: 'asdlc-*.xml', allowEmptyResults: true
        }
    }
}
```

## Integration Methods

### Method 1: `/scan` Endpoint (Recommended)

Trigger a scan with Jenkins callback URL:

```groovy
def response = httpRequest(
    acceptType: 'APPLICATION_JSON',
    contentType: 'APPLICATION_JSON',
    httpMode: 'POST',
    url: "${ASDLC_URL}/scan",
    customHeaders: [[name: 'X-API-Key', value: ASDLC_API_KEY]],
    requestBody: groovy.json.JsonOutput.toJson([
        repo_path: "${WORKSPACE}",
        branch: "${GIT_BRANCH}",
        jenkins_callback_url: "http://jenkins.example.com/job/${env.JOB_NAME}/${env.BUILD_NUMBER}/asdlc-callback",
        jenkins_job_name: "${env.JOB_NAME}",
        jenkins_build_number: "${env.BUILD_NUMBER}" as Integer,
        jenkins_api_token: JENKINS_API_TOKEN
    ])
)

def result = readJSON text: response.content
echo "asdlc run ID: ${result.run_id}"
```

**Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `repo_path` | string | Yes | Absolute path to repo on asdlc server filesystem |
| `branch` | string | No | Git branch (default: main) |
| `jenkins_callback_url` | string | Yes | URL where asdlc will POST findings |
| `jenkins_job_name` | string | No | Jenkins job name (used for API updates) |
| `jenkins_build_number` | integer | No | Jenkins build number (used for API updates) |
| `jenkins_api_token` | string | No | Jenkins API token for setting build status |

**Returns:**

```json
{
    "status": "accepted",
    "run_id": "abc123...",
    "workflow": "full_review",
    "repo": "my-app"
}
```

### Method 2: `/git/push` Webhook Headers (For Git Push Integration)

Pass Jenkins params via HTTP headers:

```
X-Jenkins-Callback-URL: http://jenkins.example.com/webhook/asdlc-callback
X-Jenkins-Job-Name: my-job
X-Jenkins-Build-Number: 42
X-Jenkins-API-Token: <token>
```

### Webhook Callback Format

When asdlc completes, it POSTs to your callback URL with:

```json
{
    "run_id": "abc123...",
    "repo": "my-app",
    "branch": "main",
    "workflow": "full_review",
    "junit_xml": "<testsuites>...</testsuites>",
    "json": {
        "repo": "my-app",
        "branch": "main",
        "workflow": "full_review",
        "started_at": "2026-01-15T10:00:00",
        "completed_at": "2026-01-15T10:02:30",
        "duration_seconds": 150,
        "total_findings": 5,
        "critical": 1,
        "high": 2,
        "medium": 2,
        "low": 0,
        "findings": [
            {
                "agent": "code_reviewer",
                "severity": "critical",
                "title": "SQL Injection Vulnerability",
                "description": "User input not sanitized in database query",
                "file_path": "app/db.py",
                "line_number": 42,
                "recommendation": "Use parameterized queries or ORM"
            },
            ...
        ]
    }
}
```

## Setting Up Webhook Receiver

### Option A: Generic Webhook Trigger Plugin

1. Install "Generic Webhook Trigger Plugin" in Jenkins
2. Create a new Pipeline job called `asdlc-webhook-handler`
3. Configure trigger:
   - Check "Generic Webhook Trigger"
   - Token: `asdlc-callback`
   - Post content parameters:
     - json_data → `$.json`
     - junit_xml → `$.junit_xml`
     - run_id → `$.run_id`
     - repo → `$.repo`
     - branch → `$.branch`

4. Copy the pipeline from `examples/asdlc-webhook-handler.groovy`

### Option B: Custom Webhook Job

Create a freestyle job that:

1. Uses "Execute shell" to receive POST data
2. Parses JUnit XML and JSON
3. Publishes reports via "Publish JUnit test result report"

Example script:

```bash
#!/bin/bash
# Parse incoming webhook data (requires generic-webhook-trigger plugin)
echo "${json_data}" > asdlc-findings.json
echo "${junit_xml}" > asdlc-findings.xml

# Publish
junit testResults='asdlc-findings.xml'
archiveArtifacts artifacts='asdlc-findings.*'
```

### Option C: Webhook Receiver Endpoint

If you run a reverse proxy or custom webhook receiver in front of Jenkins, handle POST at:

```
http://jenkins.example.com/job/<JOB_NAME>/<BUILD_NUMBER>/asdlc-callback
```

Example pseudo-code:

```python
# In your webhook receiver
@app.post("/job/<job_name>/<build_number>/asdlc-callback")
def asdlc_callback(job_name, build_number, data):
    # Parse data
    findings = data['json']
    junit_xml = data['junit_xml']
    
    # Publish to Jenkins
    jenkins.publish_junit_report(job_name, build_number, junit_xml)
    jenkins.archive_artifacts(job_name, build_number, findings)
    
    # Optionally notify
    slack.post(f"{job_name}#{build_number}: {findings['total_findings']} findings")
```

## Results Reporting

### JUnit XML Reports

Findings are converted to JUnit format where:

- **Test Case** = one finding
- **Failure** = critical or high severity finding
- **Properties**: `file`, `line`, severity level

Jenkins will show these in:
- Build summary
- Test result trend graphs
- Failure reporting

### JSON Findings Export

Access findings programmatically via `/results` API:

```bash
curl -H "X-API-Key: $ASDLC_API_KEY" \
  "http://localhost:8088/results?repo=my-app&branch=main"
```

### Build Status & Badges

When `jenkins_api_token` is provided, asdlc sets the build description via Jenkins API:

```html
<h3>asdlc Analysis Results</h3>
<ul>
    <li>Workflow: full_review</li>
    <li>Total Findings: 5</li>
    <li>Critical: 1</li>
    <li>High: 2</li>
    <li>Duration: 150.0s</li>
</ul>
```

## Configuration

### Environment Variables (`.env`)

```env
# Jenkins API token (optional; allows build status updates)
JENKINS_DEFAULT_API_TOKEN=your-jenkins-api-token

# Existing settings
WEBHOOK_SECRET=your-webhook-secret
API_KEY=your-asdlc-api-key
```

### Per-Build Overrides

Jenkins can pass different settings per build:

```groovy
asdlcScan(
    repo_path: "${WORKSPACE}",
    branch: "${GIT_BRANCH}",
    jenkins_api_token: credentials('jenkins-api-token-prod')  // different token per env
)
```

## Troubleshooting

### Callback Never Received

**Symptom:** asdlc scan completes but webhook handler job never runs.

**Diagnosis:**
1. Check asdlc logs for callback POST attempts:
   ```
   docker logs asdlc-server 2>&1 | grep "jenkins callback"
   ```
2. Verify callback URL is reachable from asdlc server:
   ```bash
   curl -X POST http://jenkins.example.com/generic-webhook-trigger/invoke?token=asdlc-callback \
     -H "Content-Type: application/json" \
     -d '{"test": "data"}'
   ```
3. Check Jenkins firewall/security group

**Fix:**
- Ensure Jenkins is accessible from asdlc server
- Double-check callback URL in asdlcScan call
- Check Jenkins Generic Webhook Trigger token matches

### JUnit Reports Not Appearing

**Symptom:** Callback received but JUnit reports don't show in Jenkins build.

**Diagnosis:**
1. Check webhook handler job logs
2. Verify `junit testResults: 'asdlc-*.xml'` in post block

**Fix:**
- Ensure junit step is in `post` block (not `stages`)
- Check file path matches glob pattern
- View logs: `Pipeline Log` in webhook handler job

### Build Status Not Updated

**Symptom:** Callback received but build description is blank.

**Diagnosis:**
1. Check asdlc logs for Jenkins API calls:
   ```
   docker logs asdlc-server 2>&1 | grep "jenkins set_build_status"
   ```
2. Verify Jenkins API token is valid and has permissions

**Fix:**
- Generate new Jenkins API token
- Ensure Jenkins user has Job.EXTENDED_READ and Job.BUILD permissions
- Check Jenkins firewall allows asdlc → Jenkins API

## Advanced: Custom Webhook Handler

If you need custom logic beyond JUnit publishing, implement a webhook receiver:

```groovy
pipeline {
    agent any
    
    triggers {
        genericWebhook(token: 'asdlc-custom')
    }
    
    stages {
        stage('Handle asdlc Callback') {
            steps {
                script {
                    def payload = currentBuild.environment.get('payload')
                    def findings = readJSON text: payload.json
                    
                    // Custom logic
                    if (findings.critical > 0) {
                        // Block build
                        currentBuild.result = 'FAILURE'
                    } else if (findings.high > 2) {
                        // Unstable
                        currentBuild.result = 'UNSTABLE'
                    }
                    
                    // Notify team
                    mail(
                        subject: "asdlc findings in ${findings.repo}",
                        body: findings.toString()
                    )
                }
            }
        }
    }
}
```

## Example: Complete CI/CD Pipeline

```groovy
@Library('asdlc-shared-library') _

pipeline {
    agent any
    
    environment {
        ASDLC_URL = credentials('asdlc-server-url')
        ASDLC_API_KEY = credentials('asdlc-api-key')
        JENKINS_API_TOKEN = credentials('jenkins-api-token')
    }
    
    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }
        
        stage('Build') {
            steps {
                sh 'npm run build'
            }
        }
        
        stage('Test') {
            steps {
                sh 'npm run test'
            }
        }
        
        stage('Code Analysis (asdlc)') {
            steps {
                script {
                    asdlcScan(
                        repo_path: "${WORKSPACE}",
                        branch: "${GIT_BRANCH}",
                        asdlc_url: "${ASDLC_URL}",
                        asdlc_api_key: "${ASDLC_API_KEY}",
                        jenkins_api_token: "${JENKINS_API_TOKEN}",
                        timeout: 300
                    )
                }
            }
        }
        
        stage('Deploy') {
            when {
                branch 'main'
            }
            steps {
                sh 'npm run deploy'
            }
        }
    }
    
    post {
        always {
            junit testResults: 'asdlc-*.xml', allowEmptyResults: true
            archiveArtifacts artifacts: 'asdlc-findings.json', allowEmptyArchive: true
        }
        
        failure {
            script {
                // Notify on critical findings
                emailext(
                    subject: "Build ${BUILD_NUMBER} failed - asdlc findings detected",
                    body: "Check Jenkins console for details",
                    to: '${DEFAULT_RECIPIENTS}'
                )
            }
        }
    }
}
```

## See Also

- [Main README](../README.md) — asdlc overview
- [API Documentation](../main.py) — endpoint details
- [CLAUDE.md](../CLAUDE.md) — developer guide
