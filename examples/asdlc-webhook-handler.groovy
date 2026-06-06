/**
 * Jenkins Webhook Handler for asdlc Callback
 *
 * This script receives the asdlc callback POST and processes the findings.
 *
 * Setup:
 * 1. Create a Jenkins job called "asdlc-webhook-handler"
 * 2. Configure it with:
 *    - Generic Webhook Trigger plugin
 *    - Token: asdlc-callback
 *    - Post content parameters:
 *      - json_data (JSONPath: $.json)
 *      - junit_xml (JSONPath: $.junit_xml)
 *      - run_id (JSONPath: $.run_id)
 *      - repo (JSONPath: $.repo)
 *      - branch (JSONPath: $.branch)
 * 3. Add this script as a Pipeline job
 *
 * Usage:
 * Jenkins will automatically POST to:
 * http://jenkins.example.com/generic-webhook-trigger/invoke?token=asdlc-callback
 */

pipeline {
    agent any

    parameters {
        string(name: 'run_id', description: 'asdlc run ID')
        string(name: 'repo', description: 'Repository name')
        string(name: 'branch', description: 'Git branch')
        string(name: 'json_data', description: 'Findings as JSON')
        string(name: 'junit_xml', description: 'Findings as JUnit XML')
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

                    // Publish as JUnit report (Jenkins will parse this automatically)
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

                    def message = """
                    asdlc Analysis Complete
                    ========================
                    Repository: ${params.repo}
                    Branch: ${params.branch}
                    Run ID: ${params.run_id}

                    Findings:
                    - Total: ${total}
                    - Critical: ${critical}
                    - High: ${high}
                    """.stripIndent()

                    // Example: send to Slack
                    // slackSend(color: 'good', message: message)

                    echo message
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
