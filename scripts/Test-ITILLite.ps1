$ErrorActionPreference = "Stop"

function Get-EnvValue([string]$Name) {
    $line = Get-Content -LiteralPath ".env" | Where-Object { $_ -match "^$([regex]::Escape($Name))=(.*)$" } | Select-Object -First 1
    if (-not $line) { throw "Missing $Name in .env" }
    return ($line -split "=", 2)[1]
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $perUserDocker = Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\resources\bin"
    if (Test-Path -LiteralPath (Join-Path $perUserDocker "docker.exe")) {
        $env:PATH = "$perUserDocker;$env:PATH"
    } else {
        throw "Docker is not installed or is not available on PATH. Install and start Docker Desktop, then reopen PowerShell."
    }
}
if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    throw "Created .env from .env.example. Change its three placeholder secrets, then run this test again."
}

$adminKey = Get-EnvValue "ADMIN_KEY"
$ingestKey = Get-EnvValue "EMAIL_INGEST_KEY"
$baseUrl = "http://localhost:8080"

Write-Host "Building and starting ITIL Lite..."
docker compose up --build -d
if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed to start." }

$healthy = $false
for ($attempt = 1; $attempt -le 30; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri "$baseUrl/api/health" -TimeoutSec 3
        if ($health.status -eq "ok") { $healthy = $true; break }
    } catch {
        Start-Sleep -Seconds 2
    }
}
if (-not $healthy) {
    docker compose ps
    docker compose logs --tail 100
    throw "The application did not become healthy within 60 seconds."
}

$catalogue = Invoke-RestMethod -Uri "$baseUrl/api/catalogue"
if ($catalogue.Count -lt 1) { throw "The service catalogue is empty." }
$incident = $catalogue | Where-Object { $_.area -eq "Digital Workplace" -and $_.type -eq "Incident" } | Select-Object -First 1
if (-not $incident) { throw "The Digital Workplace incident category is missing." }
$reclassifiedCategory = $catalogue | Where-Object { $_.area -eq "AI/Data" -and $_.type -eq "Incident" } | Select-Object -First 1
if (-not $reclassifiedCategory) { throw "The AI/Data incident category is missing." }

$adminHeaders = @{ "X-Admin-Key" = $adminKey }
$fallbackBody = @{ actor = "Docker Smoke Test"; recipient = "fallback-route@example.invalid" } | ConvertTo-Json
Invoke-RestMethod -Method Patch -Uri "$baseUrl/api/routing/fallback" -Headers $adminHeaders -ContentType "application/json" -Body $fallbackBody | Out-Null
$routeBody = @{ actor = "Docker Smoke Test"; category = $incident.name; recipient = "workplace-route@example.invalid" } | ConvertTo-Json
$routing = Invoke-RestMethod -Method Patch -Uri "$baseUrl/api/routing/category" -Headers $adminHeaders -ContentType "application/json" -Body $routeBody
if ($routing.fallback_recipient -ne "fallback-route@example.invalid") { throw "Routing fallback configuration failed." }

$ticket = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/tickets" -ContentType "application/x-www-form-urlencoded" -Body @{
    requester_name = "Docker Smoke Test"
    requester_email = "docker-test@example.invalid"
    entity = "Local"
    department = "IT"
    category = $incident.name
    priority = "P3"
    subject = "Automated Docker smoke test"
    description = "This ticket verifies portal submission, routing, staff access, and workflow updates."
}
if (-not $ticket.number -or -not $ticket.email_sent -or -not $ticket.routing_email_sent) { throw "Ticket submission, confirmation, or category routing email failed." }

$tickets = Invoke-RestMethod -Uri "$baseUrl/api/tickets" -Headers $adminHeaders
if ($ticket.number -notin $tickets.number) { throw "The new ticket was not visible in the staff console API." }

$updated = Invoke-RestMethod -Method Patch -Uri "$baseUrl/api/tickets/$($ticket.number)" -Headers $adminHeaders -ContentType "application/json" -Body '{"status":"In Progress"}'
if ($updated.status -ne "In Progress") { throw "Ticket status update failed." }

$editedDescription = "Updated by the automated Docker test."
$editBody = @{ actor = "Docker Smoke Test"; description = $editedDescription } | ConvertTo-Json
$edited = Invoke-RestMethod -Method Patch -Uri "$baseUrl/api/tickets/$($ticket.number)" -Headers $adminHeaders -ContentType "application/json" -Body $editBody
if ($edited.description -ne $editedDescription) { throw "Ticket description update failed." }

$reclassifyBody = @{ actor = "Docker Smoke Test"; category = $reclassifiedCategory.name; notify_route = $true } | ConvertTo-Json
$reclassified = Invoke-RestMethod -Method Patch -Uri "$baseUrl/api/tickets/$($ticket.number)" -Headers $adminHeaders -ContentType "application/json" -Body $reclassifyBody
if ($reclassified.category -ne $reclassifiedCategory.name -or $reclassified.assignment_group -ne "Data & Analytics" -or $reclassified.notification_recipient -ne "fallback-route@example.invalid" -or -not $reclassified.routing_email_sent) { throw "Category reclassification, fallback routing, or notification failed." }

$replyBody = @{ author = "Docker Smoke Test"; body = "Core reply and activity history test." } | ConvertTo-Json
$replied = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/tickets/$($ticket.number)/replies" -Headers $adminHeaders -ContentType "application/json" -Body $replyBody
if ($replied.replies.Count -lt 1 -or $replied.events.Count -lt 2 -or -not $replied.email_sent) { throw "Ticket replies, audit history, or reply email failed." }

$capturedMail = Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:8025/api/v1/messages" -TimeoutSec 5
if ($capturedMail.StatusCode -ne 200 -or -not $capturedMail.Content.Contains($ticket.number)) { throw "Mailpit did not capture the ticket emails." }

$summary = Invoke-RestMethod -Uri "$baseUrl/api/reports/summary" -Headers $adminHeaders
if ($summary.total -lt 1 -or $null -eq $summary.open -or $null -eq $summary.age_buckets -or $null -eq $summary.by_category) { throw "ITIL reporting did not include the expected metrics." }

$messageId = "docker-smoke-$([guid]::NewGuid().ToString('N'))"
$emailHeaders = @{ "X-Ingest-Key" = $ingestKey }
$emailBody = @{
    from = "email-test@example.invalid"
    name = "Email Smoke Test"
    subject = "[SAP] Automated email intake test"
    body = "This ticket verifies protected email intake."
    message_id = $messageId
} | ConvertTo-Json
$emailTicket = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/email/intake" -Headers $emailHeaders -ContentType "application/json" -Body $emailBody
$duplicate = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/email/intake" -Headers $emailHeaders -ContentType "application/json" -Body $emailBody
if (-not $emailTicket.number -or $duplicate.status -ne "duplicate") { throw "Email intake or duplicate protection failed." }

Write-Host "PASS: ITIL Lite is healthy and the core ticket workflows passed."
Write-Host "Portal: $baseUrl"
Write-Host "Staff console: $baseUrl/admin.html"
Write-Host "Smoke-test ticket: $($ticket.number)"
