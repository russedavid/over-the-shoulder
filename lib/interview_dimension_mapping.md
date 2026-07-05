# Interview Dimension Mapping

This document maps the interview focus areas to the files in this folder, using one project as the main story: the CMS AWS Acquia FISMA Splunk log-forwarding architecture.

The goal is to go deep on one project rather than prepare several unrelated stories. The same project should cover technical depth, technical breadth, and influence, with follow-up questions used to fill any weak spots.

## Technical Depth

Interviewers are looking for architecture, scalability, and problem-solving depth.

| Focus | Best source files | What to extract |
| --- | --- | --- |
| Methodical debugging and root-cause analysis | `problem_solving.md` | Stories where you gathered data, formed hypotheses, tested theories, found the true cause, and created lasting prevention through tooling, runbooks, dashboards, or docs. |
| Architecture and scalability trade-offs | `delivery.md`, `innovation.md`, `problem_solving.md` | Stories involving phased migrations, event-processing platforms, offline sync, distributed transactions, monitoring architecture, self-healing infrastructure, or systems that had to scale across teams and traffic growth. |
| Technical judgment under constraints | `delivery.md` | Stories where you shipped valuable work despite deadlines, dependencies, broken assumptions, or changing scope. Emphasize what quality bars were non-negotiable and what you deliberately deferred. |
| Creating new technical approaches | `innovation.md` | Stories where standard approaches were insufficient and you invented a new pattern, framework, platform, or workflow that others adopted. |

Strong examples:

- `problem_solving.md`: memory leak investigation, inventory sync race condition, performance degradation caused by monitoring, distributed order consistency failure.
- `delivery.md`: payment migration narrowed to fraud detection, database migration with rollback points, event-processing platform delivery across 12 teams.
- `innovation.md`: deterministic test runner, dynamic API gateway, event-based mobile sync, self-healing infrastructure.

Preparation prompts:

- What was the original symptom, and why was the obvious fix insufficient?
- What data, instrumentation, or experiments changed your understanding?
- What architecture alternatives did you consider?
- What scalability limits, failure modes, or operational risks shaped the decision?
- What changed after your work shipped?

## Technical Breadth

Interviewers are looking for systems thinking and cross-functional work.

| Focus | Best source files | What to extract |
| --- | --- | --- |
| End-to-end systems thinking | `problem_solving.md`, `delivery.md` | Stories where the issue crossed service, team, vendor, infrastructure, data, or product boundaries. Show how you mapped the whole system rather than only your component. |
| Cross-team execution | `delivery.md`, `earning_trust.md` | Stories involving multiple teams, competing priorities, migration planning, dependency management, and stakeholder communication. |
| Product, security, support, legal, or business context | `delivery.md`, `earning_trust.md`, `developing_others.md` | Stories where you translated technical constraints into decisions other functions could act on. |
| Reusable organizational patterns | `innovation.md`, `problem_solving.md`, `developing_others.md` | Stories where your solution became a shared framework, standard, process, dashboard, training, or review model. |

Strong examples:

- `earning_trust.md`: mobile/backend debugging relationship, cross-team API migration, product-security conflict resolved through shared patterns and OKRs.
- `delivery.md`: notification system prioritization using support-ticket data, event-processing platform balancing finance, marketing, security, and customer needs.
- `problem_solving.md`: cross-team performance degradation investigation and vendor-change communication channel.
- `developing_others.md`: translating technical concepts for product managers, designers, sales engineers, and other non-engineering partners.

Preparation prompts:

- Which teams or functions were affected?
- What incentives or constraints did each group have?
- How did you translate technical details into business, product, security, or operational impact?
- How did your work reduce duplicated effort or improve decisions outside your immediate team?
- What did you learn about the larger system?

## Influence

Interviewers are looking for mentoring, alignment, and stakeholder management.

| Focus | Best source files | What to extract |
| --- | --- | --- |
| Mentoring and capability building | `developing_others.md` | Stories where you helped someone become more independent through pairing, feedback, stretch work, documentation, or runbooks. |
| Alignment without authority | `earning_trust.md`, `delivery.md`, `innovation.md` | Stories where you got buy-in through evidence, prototypes, transparent communication, shared goals, or incremental adoption. |
| Stakeholder management | `delivery.md`, `earning_trust.md` | Stories where you communicated risk early, reset expectations, handled conflict, or kept leaders and partner teams aligned through changes. |
| Scaling influence | `developing_others.md`, `innovation.md`, `problem_solving.md` | Stories where you moved beyond one-on-one help by creating reusable resources, standards, tools, workshops, or communities. |

Strong examples:

- `developing_others.md`: Ian becoming independent on-call, scalable mentoring resources, developing people without management authority.
- `earning_trust.md`: rebuilding trust through transparency, owning a migration mistake, mediating product-security conflict.
- `delivery.md`: stakeholder expectation management during deadline pressure, prioritizing transactional notifications over competing feature requests.
- `innovation.md`: convincing others to adopt a novel approach through practical validation and gradual rollout.

Preparation prompts:

- Who needed to be convinced, coached, or aligned?
- What resistance, conflict, or skepticism existed?
- What did you do to earn trust before asking others to change?
- How did you communicate trade-offs and risks?
- How did behavior change after your involvement?

## Single Project Coverage

Project: CMS AWS Acquia FISMA Splunk log-forwarding automation.

Target level: team lead level. At FAANG this maps closer to senior engineer; at many other companies it may be described as staff. The story should emphasize end-to-end technical ownership, pragmatic architecture, delivery under real constraints, mentoring, and cross-functional alignment without overstating it as principal-level organizational strategy.

Core problem: CMS needed a consistent, robust way to get logs from hundreds of managed Drupal environments in Acquia into Splunk Enterprise. The system needed to automate log-forwarding endpoint management as Acquia environments were created, changed, or removed; keep the Universal Forwarder configuration current; maintain secure connectivity; and provide observability into log flow and endpoint health.

High-level solution: Deploy two ECS Fargate services. One Go-based orchestration engine polls Acquia APIs, compares current Acquia state against DynamoDB, commissions and decommissions forwarding endpoints, updates AWS and Universal Forwarder configuration, rolls out configuration changes, and emits health metrics. A second ECS service runs Splunk Universal Forwarders behind an NLB, with NGINX preserving source IP through Proxy Protocol V2 and routing traffic based on IP-to-environment maps. Configuration is distributed through S3/shared volumes, with metadata enrichment and forwarding into Splunk Enterprise.

## Headline and Key Ideas

Interview headline:

I designed the end-to-end architecture for a secure, automated Splunk log-forwarding platform that let CMS reliably ingest logs from 17 Acquia-hosted government website applications, across up to roughly 15 environments each, while closing a production compliance gap and automatically provisioning Splunk forwarding for new environments within five minutes.

Shorter headline:

I designed a high-scale, compliance-critical log-forwarding platform that automated Splunk ingestion for CMS's Acquia Drupal environments and replaced fragile manual reconciliation with secure, observable orchestration.

Key ideas to land:

1. This was a deep architecture problem, not a feature build. The work spanned Acquia Cloud, AWS ECS/Fargate, NLB/ALB networking, Splunk Universal Forwarders, DynamoDB state, S3/shared configuration, routing/source validation, dashboards, alarms, and production audit requirements.
2. The impact was compliance and security posture. Before this, environments could come and go without logs reliably landing in Splunk. The new system passed production auditing, reduced missing-log risk, and gave security teams a more dependable source of data for analysis.
3. The hardest technical problem was routing and source validation. The naive design required complicated per-environment external ports and IP allowlists. Acquia's source IP data was incomplete for some environment types, and mutual TLS would have created heavy quarterly certificate-rotation work. I designed a routing layer that validated an API key from syslog metadata, parsed an internal routing port, and forwarded locally to the correct Universal Forwarder port.
4. The design was scalable and validated under load. I calculated peak open-enrollment traffic at about 2k logs/events per second from existing Splunk data and tested the new path at roughly 2x peak TPS and 2x expected concurrent connections. CPU and memory stayed within safe limits, no connections dropped, and the routing layer had virtually no performance cost.
5. The system was automated and resilient. The orchestration engine took recurring snapshots of Acquia environments, compared them against DynamoDB state, triggered workflows for new/removed/changed environments, used a DynamoDB workflow lock to prevent duplicate orchestration, retried Acquia API failures with capped exponential backoff, and preserved the existing forwarder if a rollout failed.
6. The story includes influence, not just architecture. I aligned CMS Admins, DWO, Splunk admins, security, Acquia, compliance, and infrastructure stakeholders. The main alignment challenge was convincing stakeholders that routing-layer API-key validation could meet the functional source-validation requirement with much less operational burden than mutual TLS.
7. The story includes mentoring. During load testing, I coached a junior engineer who was blocked on CMS-standard load-testing tooling to reason from first principles. He built the TCP/log-generation prototype that became the basis for the DEV load test, and later applied similar thinking to integration tests that simulated environments being added and deleted.

## Coverage Matrix

| Interview area | Status | Evidence from this project | Follow-up needed |
| --- | --- | --- | --- |
| Architecture | Covered | You owned the end-to-end solution design: ECS Fargate services, NLB for long-lived TCP log traffic, ALB for internal API/status access, routing/source-validation layer, NGINX/reverse proxy behavior, Splunk Universal Forwarders, S3 config distribution, DynamoDB state tables, automation workflows, security boundaries, and observability. | Clarify the most important architecture alternatives rejected. |
| Scalability | Covered | Hundreds of managed Drupal environments, identical UF task configuration to support scale-out, autoscaling policies, dynamic configuration generation, source validation through routing metadata, and load testing at 2x observed peak TPS plus 2x anticipated concurrent connections. Peak open-enrollment traffic was about 2k TPS, so the test target was about 4k TPS. The load test completed successfully, CPU and memory remained within safe limits, and there were no dropped connections. | Covered. |
| Problem-solving depth | Partially covered | You started unfamiliar with Splunk, researched how enterprise Splunk receives logs, evaluated how Acquia could register a forwarding endpoint, and designed a path for long-lived TCP traffic inside the VPN. | Need the investigation trail: options considered, experiments run, dead ends, and why the final design was correct. |
| Security and compliance | Covered | FISMA context, VPN/internal placement, source validation requirement, API-key validation in the routing layer, protection against random internet traffic, spoofed log sources, and DoS-style unwanted traffic, certificates for secure connectivity, internal CMS allowlist for ALB access, and production audit success. The validation approach was framed as meeting the functional source-validation requirement with lower operational burden than mutual TLS, not as being cryptographically identical to mutual TLS. | Covered. |
| Operational observability | Covered | Metrics for applications, environments, functioning/non-functioning endpoints, created/updated/removed environments, log count thresholds, status history, dashboard/API visibility for DWO staff, production zero-log detection backed by regular synthetic/canary traffic, suspicious-activity detection even inside the VPN, and paging to the on-call engineer when health checks or log-flow expectations failed. No pages had fired yet, but the runbook path depended on the alert type and exposed useful first-check information such as the last deploy, whether it succeeded, and related rollout/configuration state. | Covered. |
| Automation | Covered | Acquia API polling, DynamoDB comparison, automated commissioning/decommissioning workflows, DynamoDB workflow lock to prevent concurrent orchestration, capped exponential backoff for Acquia API failures, stale DynamoDB state as drift detection, idempotent endpoint reconciliation by retrieving and managing existing Acquia endpoints before creating new ones, simple repair path through manual reconfiguration or delete-and-recreate on the next orchestration cycle, conservative UF rollout behavior that keeps the existing forwarder running on failed rollout, UF and AWS configuration generation, rollout of updated UF config. | Covered. |
| Delivery under constraints | Covered | The design addressed a compliance/monitoring gap and removed manual configuration risk while open enrollment was happening. The DWO program manager had invested significant effort to get the project prioritized over other possible work, which created pressure to deliver within the promised timeframe. The MVP shipped the core orchestration first, with basic metrics and Splunk queries for identifying failures and alarming. Later phases added a front-end showing environments and health status, then deeper health validation by surfacing UF stdout counts and comparing received-log counts against logs observed in Splunk. | Covered. |
| Cross-system breadth | Covered | Acquia Cloud Platform, AWS ECS/Fargate, NLB/ALB, DynamoDB, S3/shared config, NGINX, Splunk Universal Forwarder, Splunk Enterprise, internal CMS users. | Need which teams owned each part and how handoffs worked. |
| Cross-functional work | Covered | Stakeholders included CMS Admins, DWO, ADO teams, Splunk admins, security, infrastructure, Acquia, and compliance. CMS Admins set high-level goals and sometimes had priority requests. DWO, the Division of Website Operations, reports to CMS Admins and directly manages CMS website operations for properties like medicare.gov and healthcare.gov. ADOs are application development organizations; my team was an ADO responsible for programs/projects like ALOHA. Splunk admins provided ingestion guidance, supported observability queries, and made sure our work did not degrade the Splunk Enterprise instance. Security relied on the logs for threat monitoring and managed pen testing/security review. We represented the infrastructure side because we managed our own AWS infrastructure and parts of platform infrastructure operations. Acquia provided the Drupal service and embedded engineering support through office hours, Slack, and escalation to deeper technical engineers. Compliance was involved because ALOHA enhanced data retention and security monitoring. | Need how you translated the architecture for each group. |
| Mentoring | Covered | During load testing, a junior engineer initially got stuck trying to navigate CMS-standard load-testing-as-a-service options, access processes, documentation gaps, and scheduling blockers. You reframed the problem with him: the immediate goal was a sanity check, not satisfying a compliance process. By asking what he would do if those offerings did not exist, you helped him reason from first principles. He identified that the test only needed to open TCP connections and send log traffic, then independently built a working prototype script within a few hours. That prototype became the basis for the DEV load test and validated that the routing layer had virtually no performance cost. He later demonstrated that same kind of first-principles thinking in integration-testing work, suggesting ways to simulate environments being added and deleted so the orchestration workflows could be tested more realistically. | Covered. |
| Alignment and influence | Covered | The architecture required agreement on Splunk ingestion, Acquia endpoint registration, AWS networking, security boundaries, and operational monitoring. Key points of contention included serverless vs ECS, whether to proceed with mutual TLS and quarterly certificate rotation or adopt routing-layer validation, metadata changes that could disrupt existing security analytics, and the assumption that Acquia could provide complete source IP lists. You sold the routing-layer validation approach by breaking down the functional purpose of mutual TLS versus API-key validation: both were validating the source of traffic. You then paired the alternative with runbooks and automatic ticketing for API-key rotation, showing that it met the security need with far less operational burden. | Covered. |
| Stakeholder management | Covered | The system provided UI/API visibility for DWO staff and operational confidence for CMS admins. There was direct stakeholder pressure to accept burdensome mutual TLS certificate rotation, but you found and advocated for an alternative that preserved the development plan. You also had to explain why ECS fit better than serverless, preserve metadata continuity for security analysis, and prove with observed traffic that Acquia-provided IP lists did not cover all real sources. You framed trade-offs in terms of operational effort, security purpose, and long-term maintainability. | Covered. |
| Impact | Covered | Reduced risk of missing critical logs, closed compliance and monitoring gaps, avoided manual configuration drift, enabled scaling as environments fluctuated, and improved the organization's security posture. The scope covered 17 Acquia applications representing government websites including medicare.gov, healthcare.gov, cms.gov, data.healthcare.gov, data.medicare.gov, insurekidsnow.gov, data.medicaid.gov, and medicaid.gov, with each application having up to about 15 environments for production and multiple development/test purposes. Existing Splunk data showed peak open-enrollment traffic around 2k logs/events per second, and the system was load tested at roughly 2x that peak. The system replaced a manual process where someone periodically generated environment lists, compared them to prior lists, and manually reconciled log-forwarding configuration and metadata files. Automation reduced time-consuming manual work and, more importantly, reduced security/compliance risk from misconfiguration or environments appearing/disappearing without Splunk forwarding. The project passed production auditing and massively reduced the compliance gap where logs were not being stored in Splunk. After launch, new environments had Splunk log forwarding automatically provisioned within five minutes. | Covered. |

## Current Story Spine

Draft headline:

I designed and helped deliver an automated AWS ECS-based Splunk log-forwarding platform that let CMS reliably ingest logs from hundreds of managed Acquia Drupal environments into Splunk Enterprise, replacing manual configuration with secure, observable, dynamically managed forwarding.

Draft three-point structure:

1. Technical discovery and architecture: I had to learn how enterprise Splunk should receive these logs, determine how Acquia could forward long-lived TCP traffic to us, and design an ECS/NLB/UF architecture inside the VPN that fit CMS security constraints.
2. Automation and operability: I designed a Go orchestration engine to poll Acquia, track state in DynamoDB, commission and decommission endpoints, generate UF and AWS configuration, and emit metrics so teams could see whether log forwarding was active and healthy.
3. Scale and influence: The design supported hundreds of environments through repeatable UF tasks, dynamic NGINX and Splunk config, autoscaling, and dashboards/API visibility for CMS and DWO staff, reducing manual work and compliance risk.

## Open Questions To Fill Gaps

We will work through these one section at a time and update this document as answers become clear.

### Section 1: Technical Discovery and Architecture

Interview questions:

1. When you were unfamiliar with Splunk, what ingestion options did you evaluate for receiving Acquia logs? For example: Universal Forwarder, Splunk TCP input, HTTP Event Collector, syslog, heavy forwarder, or another intermediary.
   - Answer: The two main options considered were an HTTP Event Collector setup and Splunk Universal Forwarder instances.
2. What made the Universal Forwarder approach the right choice for this environment?
   - Answer: Research into HTTP Event Collector showed that the existing enterprise Splunk HEC endpoints were already operating at capacity. In discussions with another team, I also learned they had to run a dead-letter queue because delivery through HEC had been unreliable. That made HEC a risky fit for a compliance-sensitive log pipeline, so Universal Forwarders became the safer ingestion path.
3. What options did you reject, and why?
   - Answer: Beyond HEC, there were other Splunk instance types that could theoretically have been used. In the CMS context, though, Universal Forwarder was already a known and supportable pattern. Management configurations already existed for configuring UFs to forward into the Splunk Enterprise instance, so using UF reduced operational risk and fit the existing CMS Splunk model.
4. What was the hardest architecture constraint: Acquia endpoint registration, long-lived TCP traffic, VPN/internal routing, Splunk Enterprise expectations, certificate/security handling, or something else?
   - Answer: The hardest architectural constraint was routing. The design had to route Acquia log traffic correctly without requiring a complicated round-robin NLB setup. A major part of the architecture work was transforming the design into something simpler and more maintainable.
   - Follow-up answer: The original approach would have registered Acquia endpoints with a different external port for each Drupal environment so the system could distinguish environments and attach the right metadata. I avoided that complexity by designing a routing layer between the reverse proxy and the Universal Forwarder instances. I used an Acquia configuration option to add an API key and an internal routing port into a metadata field in the syslog RFC. The routing layer validated the API key, parsed the internal routing port, and forwarded traffic over the localhost network to the corresponding Universal Forwarder port.
   - Additional context: This design solved both routing and source validation. There was a requirement to accept traffic only from valid sources. The initial plan was to build an allowlist from source IP addresses that Acquia advertised through its API for log-forwarding endpoints. A significant amount of time went into proving that this was not fully reliable: Acquia could generally provide source IPs, but some environment types could not provide them at all. The apparent fallback was mutual TLS, but that would have created substantial operational burden because CMS policies and certificate acquisition processes made quarterly certificate rotation difficult. This became a point of contention with a CMS stakeholder who wanted to proceed with mutual TLS despite the burden. The metadata-based routing approach provided an alternative in time: the routing layer could validate an API key from the syslog metadata field and route traffic correctly without taking on the certificate-rotation workload, which kept the development plan on track.
5. What part of the architecture did you personally design or drive?
   - Answer: I designed every aspect of the solution. That included the ingestion approach, ECS/Fargate service structure, load-balancing model, routing/source-validation layer, Universal Forwarder configuration model, Acquia polling/orchestration workflows, DynamoDB state model, configuration rollout path, security boundaries, and observability strategy.

### Section 2: Scalability and Load Testing

Interview questions:

1. What traffic volume did you need to support: number of Acquia environments, concurrent TCP connections, logs per minute, data volume, or peak bursts?
   - Answer: I analyzed peak TPS from existing logs in Splunk. Peak traffic during open enrollment was about 2k TPS, so I tested at 2x that level, with 2x the anticipated concurrent TCP connections.
2. How did you load test the NLB, NGINX, UF containers, and Splunk receiving path?
   - Answer: I asked a junior engineer to implement load testing in the DEV environment. He initially researched CMS-standard load-testing-as-a-service solutions, but got stuck on access, documentation, and scheduling blockers. Since the goal was an engineering sanity check rather than a compliance checkbox, I coached him to reason from first principles: if those load-testing offerings did not exist, how would we proceed? He realized the test only needed to open TCP connections to the log-forwarding service and send a large volume of logs. A few hours later, he had a working prototype script that could hit the DEV instance, and that script became the basis for the load test. We cross-referenced instance CPU and memory usage against the generated load.
3. What failed first during load testing?
   - Answer: Nothing significant failed during the load test. The test completed successfully at the target load.
4. What tuning or design changes came out of load testing?
   - Answer: The load test showed that the additional routing layer created virtually no performance hit, so no major redesign was needed from a performance standpoint.
   - Sufficiency criteria: I considered the test sufficient because 2x peak TPS and 2x anticipated concurrent connections stayed within safe CPU and memory limits, and there were no dropped connections.
5. Where did mentoring show up in the load-testing work?
   - Answer: The mentoring happened when the junior engineer got stuck on institutional tooling and process blockers. Rather than telling him what to build, I asked questions that helped him distinguish the actual technical goal from the assumed process. That led him to design and prototype a simpler open-ended load-test script himself.

### Section 3: Automation and Failure Handling

Interview questions:

1. How did the orchestration engine prevent duplicate or concurrent commissioning for the same environment?
   - Answer: The orchestration engine used a DynamoDB entry as a workflow lock, so only the most recent orchestration-engine instance could acquire the lock and proceed with the workflow.
2. What happened if Acquia API calls failed, DynamoDB state was stale, or UF rollout failed?
   - Answer: For Acquia API failures, we implemented retries with capped exponential backoff. Stale DynamoDB state was not treated as a failure; it was part of the design. The crawler compared current Acquia state against DynamoDB, and when it found an environment that did not exist in DynamoDB, that drift triggered the provisioning workflow. If a Universal Forwarder rollout failed, the existing forwarder was not decommissioned. An alarm was triggered, and the rollout was attempted again at the end of the next orchestration period.
3. How did you make commissioning and decommissioning idempotent?
   - Answer: Part of the workflow was retrieving information about existing Acquia log-forwarding endpoints first. If an endpoint already existed, the orchestration engine managed or reconciled that endpoint rather than blindly creating another one.
4. What rollback or repair path existed when a forwarding endpoint was misconfigured?
   - Answer: A forwarding endpoint could be manually reconfigured if needed. Alternatively, it could be deleted, and the orchestration engine would recreate it on the next run.
5. How did the system distinguish an expected zero-log period from a broken forwarding path?
   - Answer: Zero-log detection applied to production instances. Production instances were not expected to be truly quiet because they had synthetics/canaries running on a regular cadence, in addition to organic traffic. That made sustained zero logs from production a meaningful signal that the forwarding path needed review.

### Section 4: Cross-Functional Influence

Interview questions:

1. Who were the main stakeholders: CMS admins, DWO staff, Splunk admins, security, infrastructure, Acquia contacts, compliance, or application owners?
   - Answer: CMS Admins were responsible for high-level goals and sometimes had specific requests tied to priority projects. DWO, the Division of Website Operations, reported to CMS Admins and was responsible for operating CMS websites such as medicare.gov and healthcare.gov; they directly managed projects like this one at a high level. ADOs, or Application Development Organizations, were the delivery organizations; my team was an ADO responsible for programs and projects like ALOHA. Splunk admins were responsible for giving us the information needed to get logs into Splunk, supporting queries for observability and health monitoring, and ensuring our work did not degrade the broader Splunk Enterprise instance. Security relied on these logs for security analysis, threat monitoring, pen testing, and security review. We represented the infrastructure side because we managed our own infrastructure and provided aspects of platform infrastructure operations. Acquia provided the Drupal service and had an embedded engineer contact who hosted office hours, answered Slack questions, and escalated deep technical questions to Acquia engineers. Compliance was involved because ALOHA enhanced data retention for legal and security purposes.
2. What did each stakeholder care about most?
   - Answer: CMS Admins cared about meeting high-level goals handed down to them, including priorities from the White House. DWO cared about getting programs and new projects running, avoiding security or compliance breaches, and making sure mainline CMS websites did not have outages. Splunk admins cared about the availability and health of the Splunk Enterprise service and making sure our ingestion work did not degrade it. Acquia cared about keeping DWO satisfied with the service so CMS would not explore other options. Compliance cared about the letter of the law, governance, and defensible data retention/security controls.
3. Where did stakeholders disagree or hesitate?
   - Answer: They initially wanted a serverless architecture, but accepted ECS once I explained why it fit the workload better. Another point of tension came when I changed the metadata structure to support a different routing model; stakeholders were concerned because security wanted continuity so their existing analysis and aggregations would keep working without changes. They were also insistent that we should be able to get a complete source-IP list from Acquia. I had to prove that the IPs Acquia provided did not cover every IP we saw in practice, and then our Acquia contact had to research the discrepancy and follow up.
4. How did you explain the design to non-Splunk or non-AWS stakeholders?
   - Answer: I explained it as a snapshot-and-reconcile system. We take a snapshot of all Acquia environments and their log-forwarding state. If an environment does not have log-forwarding set up correctly, we set it up. Every time we take a new snapshot, we save it and compare it to the previous snapshot. Differences trigger workflows: a new environment triggers log-forwarding provisioning, and a missing environment triggers cleanup of old metadata. For the Splunk-specific part, I explained that Splunk is particular about how metadata is assigned to logs. We assign a number to each environment and map that number to the environment metadata. When a log comes in with that number, we can tell Splunk to attach the correct metadata.
5. What decision required the most alignment?
   - Answer: The decision that required the most alignment was routing-layer validation over mutual TLS or IP allowlists. It touched security, compliance, operational workload, Acquia constraints, and the core architecture.
6. How did you sell the routing-layer validation alternative to the CMS stakeholder who wanted mutual TLS?
   - Answer: I broke down the functional purpose of mutual TLS versus the API-key validation approach and showed that both served the same core goal: validating the source of traffic. Then I explained that we would create runbooks for rotating the API key and set up automatic ticketing for credential rotation. That showed the stakeholder that the alternative still addressed the security need while saving significant operational effort over time compared with quarterly mutual TLS certificate rotation.

### Section 8: Security and Observability Framing

Interview questions:

1. In security terms, what was the routing-layer/API-key validation protecting against?
   - Answer: It protected against random internet traffic, spoofed log sources, and DoS-style unwanted traffic. It also gave us a way to identify suspicious activity quickly, including suspicious activity originating from inside the VPN. The important framing is that API-key validation was not identical to mutual TLS cryptographically, but it met the project's functional source-validation requirement while avoiding the operational burden of quarterly mutual TLS certificate rotation.
2. Who was alerted when log forwarding or source validation looked unhealthy?
   - Answer: The on-call engineer would be paged. At the time of this story, no pages had fired yet.
3. When the on-call engineer got paged, what did the runbook tell them to check or do first?
   - Answer: The response depended on what they were paged for, but the dashboard/runbook exposed first-check information like when the last deploy happened, whether it was successful, and related rollout/configuration state.

### Section 5: Mentoring

Interview questions:

1. Who did you mentor during this project?
   - Partial answer: A junior engineer working on DEV load testing.
2. What skill or judgment gap were you helping them build?
   - Partial answer: The main skill was problem framing under ambiguity: separating the actual engineering objective from the institutional process he initially assumed was required.
3. How did you teach through the work instead of just assigning tasks?
   - Partial answer: I asked him how he had arrived at the standard load-testing options, what blocked him, and what he would do if those tools were unavailable. This moved him toward first-principles thinking without taking the problem away from him.
4. What did they eventually own independently?
   - Partial answer: He independently prototyped the TCP/log-generation script that became the basis for the DEV load test.
5. How did their growth improve the project or team?
   - Answer: He went from blocked on institutional tooling to independently building the TCP/log-generation prototype we used for DEV load testing. That unblocked the project, let us validate CPU and memory behavior under load, and confirmed that the routing layer had virtually no performance cost. The mentoring signal is that I did not take the work away from him; I helped him reframe the problem so he could solve it himself. He later demonstrated that same kind of first-principles thinking in integration-testing work, suggesting ways to simulate environments being added and deleted so the orchestration workflows could be tested more realistically.

### Section 6: Measurable Impact

Interview questions:

1. How many Acquia applications/environments did this support?
   - Answer: It supported 17 Acquia applications representing government websites including medicare.gov, healthcare.gov, cms.gov, data.healthcare.gov, data.medicare.gov, insurekidsnow.gov, data.medicaid.gov, and medicaid.gov. Each application could have up to about 15 environments for different purposes, including production, test, and multiple development environments.
2. What log volume did the system handle?
   - Answer: I do not remember the exact daily log volume. The strongest defensible metric is that I queried existing Splunk data and calculated approximate peak open-enrollment traffic at about 2k logs/events per second. I then load tested the new architecture at roughly 2x that peak.
3. What manual process did this replace, and how much time or risk did it remove?
   - Answer: Previously, an individual periodically generated a list of environments, compared it to the previous list, and manually reconciled changes by configuring log forwarding and editing metadata files as needed. Besides being time consuming, that process created a significant risk of misconfiguration and of environments being created or removed without their logs being forwarded to Splunk. The biggest impact was reducing security and compliance risk.
4. Did it pass an audit, close a compliance gap, reduce missing logs, or improve incident response?
   - Answer: Yes. The project passed all production auditing and closed the compliance gap where logs were not being stored in Splunk. It massively reduced that gap and improved the organization's security posture.
5. What happened after launch that proves the architecture worked?
   - Answer: Any time a new environment was spun up, log forwarding to Splunk was automatically provisioned within five minutes.

### Section 7: Delivery Under Constraints

Interview questions:

1. What was the timeline or delivery constraint?
   - Answer: Open enrollment was happening while the project was being worked. The DWO program manager driving the project had invested significant time and energy to get it prioritized over other possible projects, so there was pressure to deliver within the promised timeframe.
2. What shipped first as the MVP, and what was deferred?
   - Answer: The core orchestration shipped first, with basic metrics and Splunk queries for identifying failures and alarming. After that, we shipped a front-end that showed environments and their health: whether log forwarding was provisioned and whether logs were flowing as expected. Eventually we also surfaced Universal Forwarder stdout, which logged how many logs were received, and compared that to the number of logs in Splunk as an additional health metric.

## How To Prepare The Example

Use a tight structure:

1. Headline: one sentence with the problem, your role, and the result.
2. Context: what made the situation technically or organizationally hard.
3. Decision path: the key technical choices, alternatives rejected, and trade-offs.
4. Influence path: who needed alignment, what concerns they had, and how you handled them.
5. Impact: shipped outcome, measurable improvement, adoption, reduced risk, or team capability gained.
6. Reflection: what you would repeat, what you would change, and what principle you learned.

For this story, be ready to answer:

- What exactly did you own?
- What was hard technically?
- What was hard organizationally?
- What options did you reject and why?
- How did you validate that the solution worked?
- Who benefited, and how do you know?
- How did the work continue after you were done?

## Final Story Guidance

This story should include at least one concrete outcome: reliable log ingestion, reduced manual configuration, fewer missing-log incidents, improved audit readiness, faster commissioning of new Acquia environments, or a teammate becoming independent through mentoring.

Avoid making the story only about the architecture. The strongest version connects technical decisions to durable outcomes and shows how you brought people with you.
