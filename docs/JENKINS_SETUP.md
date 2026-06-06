# Jenkins Setup Guide for asdlc Integration

This guide walks you through setting up Jenkins with asdlc for your `inventory-tracker` app.

## Step 1: Start Jenkins on Docker

```bash
cd /Users/asaf/Projects/agentic-sdlc

# Start Jenkins
docker-compose -f jenkins-docker-compose.yml up -d

# Wait for startup (30-60 seconds)
sleep 30

# Get initial admin password
docker exec jenkins-asdlc cat /var/jenkins_home/secrets/initialAdminPassword
```

Jenkins will be available at: **http://localhost:8080**

## Step 2: Initial Jenkins Setup

1. Go to **http://localhost:8080**
2. Paste the admin password from above
3. Click "Install suggested plugins" (or just "Install plugins")
4. Create first admin user:
   - Username: `admin`
   - Password: `your-secure-password`
5. Jenkins URL: `http://localhost:8080/`

## Step 3: Generate Jenkins API Token

1. Go to Jenkins dashboard → **Manage Jenkins** (top left)
2. Click **Users** → **admin** (your username)
3. Click **Configure** (left sidebar)
4. Under "API Token" section, click **Add new Token**
5. Name it: `asdlc-token`
6. Copy the token (you'll need this in Step 5)

## Step 4: Create Credentials for asdlc

1. Go to Jenkins dashboard → **Manage Jenkins** → **Manage Credentials**
2. Click **System** → **Global credentials (unrestricted)**
3. Click **+ Add Credentials** and create 3 secrets:

### Credential 1: asdlc Server URL
- Kind: **Secret text**
- Secret: `http://localhost:8088` (or your asdlc server URL)
- ID: `asdlc-server-url`
- Description: `asdlc Server URL`

### Credential 2: asdlc API Key
- Kind: **Secret text**
- Secret: `your-asdlc-api-key` (from asdlc `.env` file, the `API_KEY` value)
- ID: `asdlc-api-key`
- Description: `asdlc API Key`

### Credential 3: Jenkins API Token
- Kind: **Secret text**
- Secret: `<paste the token from Step 3>`
- ID: `jenkins-api-token`
- Description: `Jenkins API Token for asdlc`

## Step 5: Create Webhook Handler Job

This job receives asdlc findings and publishes reports.

1. Go to Jenkins dashboard
2. Click **+ New Item** (top left)
3. Enter name: `asdlc-webhook-handler`
4. Select **Pipeline** → **OK**

### Configure Trigger

1. Check **Generic Webhook Trigger**
2. Set Token: `asdlc-callback`
3. In "Post content parameters", add:
   - Name: `json_data`, Expression: `$.json`
   - Name: `junit_xml`, Expression: `$.junit_xml`
   - Name: `run_id`, Expression: `$.run_id`
   - Name: `repo`, Expression: `$.repo`
   - Name: `branch`, Expression: `$.branch`

### Add Pipeline Script

1. In "Pipeline" section, select **Definition: Pipeline script**
2. Paste this script:

```groovy
pipeline {
    agent any

    parameters {
        string(name: 'run_id', defaultValue: '', description: 'asdlc run ID')
        string(name: 'repo', defaultValue: '', description: 'Repository name')
        string(name: 'branch', defaultValue: '', description: 'Git branch')
        string(name: 'json_data', defaultValue: '{}', description: 'Findings as JSON')
        string(name: 'junit_xml', defaultValue: '', description: 'Findings as JUnit XML')
    }

    stages {
        stage('Receive Callback') {
            steps {
                echo "Received asdlc callback"
                echo "  run_id: ${params.run_id}"
                echo "  repo: ${params.repo}"
                echo "  branch: ${params.branch}"

                script {
                    // Parse the JSON data
                    def findings = readJSON text: params.json_data

                    echo "Total findings: ${findings.total_findings}"
                    echo "  Critical: ${findings.critical}"
                    echo "  High: ${findings.high}"
                    echo "  Medium: ${findings.medium}"
                    echo "  Low: ${findings.low}"

                    // Store for artifact archiving
                    writeFile file: 'asdlc-findings.json', text: params.json_data
                }
            }
        }

        stage('Publish JUnit Reports') {
            steps {
                script {
                    // Write JUnit XML to file
                    writeFile file: 'asdlc-findings.xml', text: params.junit_xml

                    // Publish as JUnit report
                    junit testResults: 'asdlc-findings.xml', allowEmptyResults: true
                }
            }
        }

        stage('Archive Results') {
            steps {
                archiveArtifacts artifacts: 'asdlc-findings.*', allowEmptyArchive: true
            }
        }

        stage('Notify') {
            steps {
                script {
                    def findings = readJSON file: 'asdlc-findings.json'
                    def total = findings.total_findings
                    def critical = findings.critical
                    def high = findings.high

                    echo """
                    asdlc Analysis Complete
                    ========================
                    Repository: ${params.repo}
                    Branch: ${params.branch}
                    Run ID: ${params.run_id}

                    Findings:
                    - Total: ${total}
                    - Critical: ${critical}
                    - High: ${high}
                    """
                }
            }
        }
    }

    post {
        always {
            cleanWs()
        }
    }
}
```

3. Click **Save**

## Step 6: Create inventory-tracker Build Job

This is the main CI/CD job for inventory-tracker that will trigger asdlc scans.

1. Click **+ New Item**
2. Enter name: `inventory-tracker-ci`
3. Select **Pipeline** → **OK**

### Configure Git

1. In "Pipeline" section, select **Definition: Pipeline script from SCM**
2. Select SCM: **Git**
3. Repository URL: `file:///Users/asaf/Projects/inventory-tracker` (or wherever your repo is)
4. Branch: `*/main`

### Or: Use Inline Jenkinsfile

If you want to test quickly without SCM:

1. Select **Definition: Pipeline script** (inline)
2. Paste this script:

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
                checkout([$class: 'GitSCM', 
                    userRemoteConfigs: [[url: 'file:///Users/asaf/Projects/inventory-tracker']], 
                    branches: [[name: '*/main']]
                ])
            }
        }
        
        stage('Build') {
            steps {
                echo 'Building inventory-tracker...'
                sh '.venv/bin/pytest tests/ -v --tb=short 2>&1 | head -50 || true'
            }
        }
        
        stage('asdlc Analysis') {
            steps {
                script {
                    echo "Triggering asdlc scan..."
                    
                    def response = httpRequest(
                        acceptType: 'APPLICATION_JSON',
                        contentType: 'APPLICATION_JSON',
                        httpMode: 'POST',
                        url: "${ASDLC_URL}/scan",
                        customHeaders: [[name: 'X-API-Key', value: ASDLC_API_KEY]],
                        requestBody: groovy.json.JsonOutput.toJson([
                            repo_path: "${WORKSPACE}",
                            branch: "main",
                            jenkins_callback_url: "${BUILD_URL}asdlc-callback",
                            jenkins_job_name: "${JOB_NAME}",
                            jenkins_build_number: "${BUILD_NUMBER}" as Integer,
                            jenkins_api_token: JENKINS_API_TOKEN
                        ]),
                        validResponseCodes: '202'
                    )
                    
                    def responseBody = readJSON text: response.content
                    env.ASDLC_RUN_ID = responseBody.run_id
                    echo "asdlc scan triggered: ${env.ASDLC_RUN_ID}"
                }
            }
        }
        
        stage('Wait for Results') {
            steps {
                echo "Scan initiated. Results will arrive via webhook callback."
                echo "Run ID: ${ASDLC_RUN_ID}"
            }
        }
    }
    
    post {
        always {
            // Publish any JUnit reports generated by asdlc callback
            junit testResults: 'asdlc-*.xml', allowEmptyResults: true
        }
    }
}
```

3. Click **Save**

## Step 7: Set Up Shared Library (Optional but Recommended)

This allows you to use `@Library('asdlc-shared-library')` in Jenkinsfiles.

1. Go to **Manage Jenkins** → **Configure System**
2. Scroll to "Global Pipeline Libraries"
3. Click **Add**:
   - Name: `asdlc-shared-library`
   - Default version: `main` (or your branch)
   - Modern SCM: Select your repo type (Git)
   - Project Repository: `file:///Users/asaf/Projects/agentic-sdlc`
   - Traits: Select "Discover tags", etc.

4. Create the library structure in agentic-sdlc:
   ```
   agentic-sdlc/
   └── vars/
       └── asdlcScan.groovy  (copy from examples/asdlc-shared-library.groovy)
   ```

## Step 8: Test the Integration

### Test 1: Trigger inventory-tracker Build

1. Go to Jenkins dashboard
2. Click **inventory-tracker-ci**
3. Click **Build Now** (top left)
4. Watch console output:
   - Should see "asdlc scan triggered: <run-id>"

### Test 2: Verify asdlc Server Receives Request

In asdlc server terminal:
```bash
.venv/bin/uvicorn main:app --port 8088 --reload
# Watch logs for:
# - "POST /scan"
# - "jenkins callback" when scan completes
```

### Test 3: Check Webhook Handler Job

1. Go to Jenkins dashboard
2. Click **asdlc-webhook-handler**
3. You should see a recent build (triggered by asdlc callback)
4. Click the build → **Console Output**
5. Should see "asdlc Analysis Complete" summary

## Troubleshooting

### Jenkins can't reach asdlc server

**Symptom:** Build fails with "Connection refused" when calling `/scan`

**Fix:**
- If asdlc is on host machine: Use `http://host.docker.internal:8088` instead of `http://localhost:8088`
- If asdlc is in Docker: Use `asdlc` service name if in same docker-compose, or `http://docker-asdlc:8088`

### Webhook handler job never triggers

**Symptom:** asdlc scan completes but webhook handler job doesn't run

**Fix:**
- Check Generic Webhook Trigger token matches (should be `asdlc-callback`)
- Check asdlc logs for "jenkins callback failed"
- Verify Jenkins callback URL is reachable from asdlc
- Test manually: `curl -X POST http://localhost:8080/generic-webhook-trigger/invoke?token=asdlc-callback -d '{"test": "data"}'`

### JUnit reports not showing

**Symptom:** Webhook handler receives data but reports don't appear

**Fix:**
- Check that `junit` plugin is installed (Manage Jenkins → Manage Plugins)
- Ensure XML is valid (check webhook handler job's asdlc-findings.xml file)
- Post processing might be disabled; check job config

## Networking Notes

If running Jenkins and asdlc in Docker on the same machine:

```yaml
# docker-compose.yml
services:
  jenkins:
    networks:
      - asdlc
    environment:
      - ASDLC_URL=http://asdlc:8088
  
  asdlc:
    networks:
      - asdlc

networks:
  asdlc:
```

Then use `http://asdlc:8088` in Jenkins credentials instead of `localhost`.

## Next Steps

1. ✅ Start Jenkins: `docker-compose -f jenkins-docker-compose.yml up -d`
2. ✅ Complete Steps 2-6 above
3. ✅ Test with inventory-tracker
4. 📝 Update inventory-tracker's `Jenkinsfile` (if using SCM polling)
5. 🔗 Set up GitHub webhooks to auto-trigger builds

## See Also

- [JENKINS_INTEGRATION.md](JENKINS_INTEGRATION.md) — Technical reference
- [examples/Jenkinsfile.groovy](../examples/Jenkinsfile.groovy) — Example Jenkinsfile
- [examples/asdlc-shared-library.groovy](../examples/asdlc-shared-library.groovy) — Groovy library code
