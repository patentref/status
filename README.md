# patentref.io status probe

External uptime probe for https://patentref.io (PatentRef checklist 5.4). A GitHub Actions workflow runs probe.py every 5 minutes from GitHub's network against the API's health route and appends the result to uptime/history.json; uptime/latest.json is the newest probe. The public status page https://patentref.io/api/v1/meta/status/ computes the uptime figure from this history. The source of truth for probe.py and the workflow is ops/uptime/ in the private patentref/patentref repository; ops/monitoring.md there explains the rule.
