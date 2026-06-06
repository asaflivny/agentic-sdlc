/**
 * Jenkins Shared Library for asdlc integration
 *
 * Place this in your Jenkins shared library at:
 * vars/asdlcScan.groovy
 *
 * Usage in Jenkinsfile:
 * @Library('asdlc-shared-library') _
 * def result = asdlcScan(
 *     repo_path: '/path/to/repo',
 *     branch: 'main',
 *     asdlc_url: 'http://localhost:8088',
 *     asdlc_api_key: 'your-api-key',
 *     jenkins_api_token: 'your-jenkins-token'
 * )
 */

def call(Map config) {
    def asdlcUrl = config.asdlc_url ?: env.ASDLC_URL
    def asdlcApiKey = config.asdlc_api_key ?: env.ASDLC_API_KEY
    def repoPath = config.repo_path ?: env.WORKSPACE
    def branch = config.branch ?: env.GIT_BRANCH?.replaceAll(/^.*\//, '')
    def jenkinsApiToken = config.jenkins_api_token ?: env.JENKINS_API_TOKEN
    def timeout = config.timeout ?: 600  // seconds

    // Jenkins callback URL where asdlc will POST findings
    def callbackUrl = "${env.JENKINS_URL}job/${env.JOB_NAME}/${env.BUILD_NUMBER}/asdlc-callback"

    echo "Triggering asdlc analysis..."
    echo "  asdlc: ${asdlcUrl}"
    echo "  repo: ${repoPath}"
    echo "  branch: ${branch}"
    echo "  callback: ${callbackUrl}"

    // POST to /scan endpoint
    def scanPayload = [
        repo_path: repoPath,
        branch: branch,
        jenkins_callback_url: callbackUrl,
        jenkins_job_name: env.JOB_NAME,
        jenkins_build_number: env.BUILD_NUMBER as Integer,
        jenkins_api_token: jenkinsApiToken
    ]

    def response = httpRequest(
        acceptType: 'APPLICATION_JSON',
        contentType: 'APPLICATION_JSON',
        httpMode: 'POST',
        url: "${asdlcUrl}/scan",
        customHeaders: [[name: 'X-API-Key', value: asdlcApiKey]],
        requestBody: groovy.json.JsonOutput.toJson(scanPayload),
        validResponseCodes: '202'
    )

    def responseBody = readJSON text: response.content
    def runId = responseBody.run_id

    echo "asdlc scan accepted: ${runId}"

    // Store run_id for later polling (optional)
    writeFile file: '.asdlc-run-id', text: runId

    // Set up webhook receiver to handle callback
    registerWebhook(runId)

    return [
        run_id: runId,
        callback_url: callbackUrl
    ]
}

/**
 * Register a webhook handler for asdlc callback
 * This sets up Jenkins to receive POST data from asdlc
 */
def registerWebhook(String runId) {
    // In a real Jenkins setup, you would:
    // 1. Register a webhook endpoint at /asdlc-callback
    // 2. Have it parse the incoming JUnit XML and JSON
    // 3. Publish the results
    //
    // For now, this is documented in the separate webhook handler below
    echo "Webhook registered for run: ${runId}"
}
