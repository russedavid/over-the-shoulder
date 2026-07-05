## Interview-Ready Opening

Use this as the first two minutes:

> The project I would deep dive on is the ATX LED hub software, which was deployed across hundreds of homes. I was the principal engineer and owned the ATXLED application code end-to-end, plus the modifications we made to integrated packages. The hub was a Raspberry Pi-based edge controller installed in houses. It controlled DALI lighting hardware through a custom hat, served the local installer/customer UI, handled schedules, scenes, groups, triggers, and device recovery, and integrated with ecosystems like Hue/Home Assistant, WiZ, Z-Wave, DMX, Google Home, and Control4.
>
> The key engineering judgment was that lighting control had to be local-first. Cloud was useful for registration, remote support, telemetry, backups, and external integrations, but it could not be required for lights to work. That drove the architecture: a local Flask/gunicorn app, SQLite for durable configuration, an in-memory live state model for hardware reconciliation, a serialized DALI command path with priority locking, background monitor/scan/watchdog threads, and companion services for WiFi provisioning, release updates, health checks, and cloud reporting.
>
> The project is a good deep-dive example because it had real production constraints: hundreds of field deployments, unreliable home networks, low-level serial hardware protocols, installer workflows, source/IP protection, backwards-compatible updates, and supportability. The hard parts were not just writing APIs; they were making a physical system recoverable when devices disappeared, networks changed, scans were slow, cloud was unavailable, or a background hardware monitor got stuck.

## Six-Part Interview Structure

Use this as the spine of the 10-15 minute walkthrough. The repo details, incident candidates, diagrams, and metrics below are supporting evidence; the live answer should stay in this order.

1. Context and stakes.
   - What it did: Raspberry Pi-based ATX LED lighting hub for local control of DALI lighting hardware.
   - Who it served: customers, installers, support, factory/test workflows, and smart-home integrations.
   - Why it mattered: deployed across hundreds of houses, so reliability, recoverability, and supportability mattered as much as feature delivery.

2. Your role and decision rights.
   - Principal engineer and technical owner for the ATXLED application code.
   - Owned the modifications/integration behavior for forked or vendored packages where they affected the product.
   - Drove architecture across local runtime, DALI control, persistence, UI, provisioning, update/release flow, telemetry, and field support.
   - Requirements came from a mix of field deployments, installer workflows, support issues, hardware/firmware constraints, factory validation needs, and business constraints around shipping/supporting hubs.

3. Requirements and constraints.
   - Local control had to work without cloud availability.
   - DALI bus access had to be serialized because command sequences are stateful and hardware responses can be ambiguous.
   - Installers had to be able to commission devices from the local control server on a laptop or phone: quick scan physical devices, assign addresses, name lights, and identify missing devices.
   - Home networks were unreliable; provisioning and support needed recovery paths.
   - Updates had to preserve customer configuration on deployed hubs.
   - Factory validation needed to test real switches/DR2s before shipment to the United States.

4. Architecture and data flow.
   - Local nginx fronted ZPDS, diy-hue, WebSockets, and provisioning flows.
   - ZPDS Flask/gunicorn served APIs/UI and coordinated the live hardware model.
   - SQLite persisted configuration; `DumbDB` held live state and reconciled against DALI scans/events.
   - Background threads handled monitor/scans, schedules/triggers, heartbeat, cloud listener, WiZ/DMX/Z-Wave integrations, and watchdog recovery.
   - `cron-boom`, loader/release tooling, and `mgmt-server` handled provisioning, updates, backups, registration, and cloud command relay.

5. Decisions, trade-offs, and failure handling.
   - Lead with local-first control, DALI as the control topology, in-memory live state plus SQLite, and serialized DALI bus access.
   - Use one or two incident stories: high-device-count broadcast/macro deadlock, quick restore/write storm, hotspot/offline recovery, stale thread watchdogs, or factory test safety.
   - Explain alternatives and trade-offs: cloud-first vs local-first, full scans vs incremental reconciliation, one command queue vs locks, whole-process restarts vs targeted watchdogs.

6. Impact and hindsight.
   - Impact: production deployment across hundreds of homes, full field lifecycle support, installer/admin tooling, remote diagnostics, update flow, and factory validation.
   - Hindsight: split monoliths, formalize state/event schemas, strengthen secrets/update design, durable cloud command queues, better observability around scans, command latency, and watchdog restarts.
   - Be precise: use real metrics where known; otherwise say what the system enabled without inventing numbers.

## Requirements And Cross-Functional Collaboration

This section is important for principal-engineer interviews, but do not over-formalize it. This was not a clean product-management handoff with polished specs. A lot of requirements came through direct working loops with Murray, an engaged founder/hardware/embedded engineer who was designing and manufacturing many of the physical devices the hub interacted with. Because the company was moving quickly with limited resources, part of the engineering role was giving Murray accurate level-of-effort estimates so the team could decide what was worth doing immediately, what could be simplified, and what needed to wait.

The realistic framing is:

> Requirements often started as a concrete operational need from hardware, factory, support, or field installation work. Murray might say, "I need a screen where I can see all the debug info for a switch." A "switch" in this context meant one of the DALI control devices on the bus that controlled LED power or other lighting behavior. I would translate that into a software surface, for example adding a link from the advanced page, building the debug screen, loading it on a lab system, and asking him to review it. Just as importantly, I had to estimate the level of effort accurately enough that we could decide whether to build the full version, ship a targeted diagnostic, or defer it behind something more urgent. Sometimes the first version was exactly what he needed; sometimes he wanted fields moved, renamed, reformatted, or additional hardware data exposed. That feedback loop shaped the admin and diagnostic tooling.

### Where Requirements Came From

Founder/hardware/embedded engineering:

- Murray drove many requirements from the physical-device side: switches, DR2s, DALI memory/debug data, power behavior, firmware behavior, manufacturing needs, and what he needed to see while debugging hardware.
- The requirement might be as concrete as "I need a debug screen for this switch" rather than a full spec.
- My job was to turn that into a usable software workflow: decide where it belonged in the UI, what API/data path was needed, how to query the DALI/device state safely, and how to make it useful on a lab or field hub.
- A key part of that loop was estimation. Because engineering time was scarce, I needed to distinguish "I can add that to the advanced page today," from "that touches DALI scan behavior, firmware assumptions, and installer workflows, so we should either narrow it or prioritize it explicitly."
- The iteration loop was fast and practical: build it, load it on a lab system, have Murray use it, then adjust naming, layout, formatting, or data coverage based on his feedback.

Field deployments and installers:

- A lot of product work went into making lighting installation easier from the control server UI on an installer's laptop or iPhone.
- Installers needed fast local setup, quick scans of physical DALI devices, address assignment, device naming, groups, scenes, topology/power diagnostics, raw command tools, WiFi provisioning, and recovery workflows.
- They needed to see "missing devices" clearly when a light that had previously existed was no longer responding at that DALI address.
- Requirements were shaped by real homes: unreliable WiFi, changing IPs, routers without clean internet access, devices that disappeared or returned partial DALI data, and support situations where someone was not physically next to the hub.
- This drove quick scanning, hotspot provisioning, offline mode, quick restore, missing-device/MIA handling, watchdogs, backups, and local-first control.

Customers and end users:

- Customers needed lights to respond locally and reliably, independent of cloud availability.
- They also expected familiar smart-home integrations: Hue/Home Assistant-style control, Google/cloud paths, WiZ rooms, Z-Wave events, DMX inputs, and Control4 discovery.
- This drove the decision to make cloud additive rather than mandatory.

Support and operations:

- Support needed enough remote visibility to diagnose deployed hubs without immediately sending someone on site.
- That drove heartbeats, remote status, cloud backups, branch/update commands, Dataplicity hooks, process health checks, and release metadata reporting.
- Supportability also affected UI density: some admin pages are not consumer-polished, but they expose the controls support/installers need.

Hardware and firmware:

- The DALI protocol, custom Pi hat, power-status reporting, firmware memory maps, serial-number assignment, and device UPC/version behavior shaped the software model.
- Hardware realities drove serialized DALI access, retries, scan queues, power-status handling, topology reads, device-memory guards, and compatibility paths for older hats or devices with partial responses.
- One good design decision was using DALI as the control layer. It meant installers generally did not have to think about physical device placement in terms of software topology, except when a house design used more than one DALI bus.

Factory and supply chain:

- The factory needed repeatable validation for control units such as switches and DR2s before shipment to the United States.
- That drove `hw_test.py`, firmware regression hooks, GPIO relay control, wattage and voltage thresholds, serial-number handling, and power-down cleanup guarantees.

Business and product constraints:

- The product needed to ship and update proprietary hub software on devices installed in customer homes.
- That drove the release loader, encrypted/obfuscated ramdisk payload, branch/tag release metadata, updater flow, and offline/key-server fallback paths.

### Collaboration Story To Tell

Use this phrasing as a bridge between role and architecture:

> The requirements were not handed to me as clean specs. A lot came from direct collaboration with Murray, who was both a founder and the hardware/embedded lead. He would have a concrete need from the lab, factory, or a customer system, like needing a screen to inspect all debug information for a DALI switch/control device. I would turn that into a working admin or diagnostic feature, deploy it to a lab hub, and iterate with him on whether the data, naming, formatting, and workflow matched how he debugged the physical device. Because we were moving fast with limited resources, I also had to give him an honest level-of-effort read: which requests were small UI/API additions, which ones required deeper DALI or firmware work, and where a narrower version would solve the immediate problem. The same pattern applied to installer feedback, support issues, factory validation, and field failures: I translated those inputs into product behavior, engineering scope, and system boundaries.

Cross-functional examples worth mentioning:

- With Murray/hardware: turned hardware-debugging needs into advanced pages, DALI debug views, power/status tooling, device-memory reads, serial-number handling, and firmware regression/test workflows.
- With installers/support: converted field pain into WiFi provisioning, hotspot/offline recovery, status pages, backups, and remote update controls.
- With hardware/firmware: encoded DALI memory/UPC/version behavior, power-status reads, serial-number handling, scan retries, and bus locking around physical protocol constraints.
- With factory/manufacturing: built repeatable pre-shipment validation for switches/DR2s, including safe power cycling and firmware regression execution.
- With product/business stakeholders: balanced local-first customer experience, smart-home integrations, remote support needs, and source/IP protection.

Facts to add from memory:

- Who were the main cross-functional partners by role: Murray/founder/hardware/embedded, factory lead, installer, support, customer, sales?
- How did requirements usually arrive: customer escalation, installer feedback, support ticket, factory failure, bench test, roadmap discussion?
- Where did level-of-effort estimates change priority, narrow scope, or turn a request into a smaller first version?
- One concrete example where you pushed back, changed scope, or made a trade-off across teams.
- One example where a field incident changed the architecture.
- One example where factory or hardware feedback changed software behavior.

## Defensible Impact Claims

These are safe without inventing exact numbers:

- Production scale: deployed across hundreds of houses.
- Product capability: enabled ATX LED to ship a local-first lighting-control hub rather than relying on a cloud-only system.
- Operational impact: created the field support backbone: heartbeats, remote status reporting, cloud backups, branch/update commands, watchdogs, hotspot provisioning, and basic remote command relay.
- Installer impact: provided dense installer/admin tooling for DALI scans, address assignment, device naming, groups, scenes, raw commands, power status, topology, provisioning, and hardware tests.
- Reliability impact: added multiple recovery layers: systemd restarts, `cron-boom` health checks, DALI and schedule watchdogs, quick restore snapshots, missing-device rescans, offline/local fallback, and cloud listener reconnects.
- Ecosystem impact: made ATX LED devices usable through local UI plus Hue/Home Assistant-style flows, Google/cloud flows, WiZ rooms, Z-Wave events, DMX inputs, and Control4 discovery.
- Engineering process impact: built simulator-backed regression coverage for DALI/API behavior and separate hardware/firmware test tooling for physical-device validation.

Exact metrics still worth adding if you know them:

- Approximate number of hubs/houses.
- Typical and maximum lights/devices per home.
- Years in production.
- Release cadence or number of release tags shipped.
- Support-call or truck-roll reduction after remote diagnostics/backups/watchdogs.
- Install-time reduction after provisioning/admin UI improvements.
- Any uptime, recovery, or update-success numbers.

## Structured System Design Response

Use this section when an interviewer asks for the project in a classic system-design format. The answer below is based on ZPDS as the system being designed: a local-first residential lighting-control hub for ATX LED homes, with DALI hardware control, installer/admin tooling, schedules/triggers, integrations, cloud support, and factory validation.

### Functional Requirements

- Users should be able to control lights, groups, scenes, color temperature, and brightness locally from the hub UI and supported smart-home integrations.
- Installers should be able to discover, address, name, group, scan, and diagnose DALI devices from the local control server using a laptop or phone during installation.
- Support users should be able to inspect hub status, device status, power status, logs, backups, software version, and remote-access/update state without immediately going on site.
- Users should be able to define schedules, triggers, scenes, and automation actions across DALI devices plus integrations such as Z-Wave, DMX, WiZ, Hue/Home Assistant-style clients, Google/cloud paths, and Control4 discovery.
- Factory and hardware users should be able to run repeatable pre-shipment tests for control units such as switches and DR2s, including DALI memory checks, firmware regression tests, power cycling, voltage/current checks, and safe cleanup.

### Non-Functional Requirements

- Local-first availability: core lighting control must keep working on the local network and in offline/hotspot mode; the cloud is additive for remote control, account linking, backups, and support.
- Hardware correctness: DALI bus operations must be serialized per bus/channel so foreground commands, scans, schedule actions, and background monitor work do not corrupt stateful serial protocol exchanges.
- Installer responsiveness: normal UI control should not block behind full bus discovery; slow scans and topology reads should stream progress and run as foreground maintenance actions or low-priority monitor work.
- Per-hub device scale: support up to 4 DALI channels and 64 short addresses per channel, or 256 DALI short addresses before counting groups, scenes, virtual devices, WiZ bulbs, Z-Wave nodes, and DMX mappings.
- DALI feature scale: support 16 DALI groups and 16 DALI scenes, with ATX-specific virtual device/group behavior layered on top.
- DMX input scale: handle 512-channel DMX frames and map channel ranges into rooms, devices, groups, or trigger behavior.
- Field reliability: watchdogs should restart stale background control loops, with DALI monitor staleness detected around 180 seconds and schedule-loop staleness around 240 seconds in the current code.
- Startup recovery: the hub should restore a useful last-known light state quickly after reboot using quick restore snapshots, then reconcile with the physical bus through scans and missing-device detection.
- Network recovery: the hub should tolerate bad WiFi, changing IP addresses, lack of internet, and customer routers that are not installer-friendly by providing hotspot/offline setup paths.
- Update operability: deployed hubs should be able to receive branch/tag updates, report release metadata, and recover through systemd/updater/cron supervision when the application is unhealthy.

### Capacity Estimation

The important capacity estimate is local hardware capacity, not web-scale QPS.

- Deployment scale: hundreds of houses, with one local hub acting as the control point for each house.
- Per-house DALI capacity: `4 channels * 64 short addresses = 256` physical DALI short addresses per hub, before virtual groups/devices and non-DALI integrations.
- Per-house protocol bottleneck: DALI is a shared serial/control bus, so throughput is constrained by bus timing and command serialization rather than CPU or HTTP capacity.
- Web/API concurrency: the production service runs as a local Flask/gunicorn app with one worker and gthread concurrency. That is reasonable because the dominant serialized resource is the DALI bus, and most concurrent users are installer/admin/browser sessions in a single house.
- State size: per-hub durable state is small enough for SQLite: device metadata, scenes, groups, schedules, site settings, quick restore snapshots, room/DMX/WiZ/Z-Wave mappings, and Hue compatibility data.
- Cloud capacity: cloud traffic is low bandwidth but long lived: registered hubs report device state, send heartbeats/status/backups, and maintain outbound command-listening behavior. The architecture avoids inbound connections to customer routers.

### Core Entities

- Hub/site
- DALI channel/bus
- DALI device/light
- Passive device
- Button/control device, including switches
- DR2/control unit
- Group and virtual group
- Scene
- Schedule entry
- Trigger condition and automation action
- Quick restore snapshot
- Missing/MIA device
- Power status and power supply
- Site settings
- Cloud registration/API token
- Release/update branch/tag
- Backup
- WiFi network/hotspot/offline mode
- Z-Wave node
- WiZ bulb
- Room
- DMX assignment
- Factory hardware test run
- Firmware regression test

### API Design

Representative ZPDS endpoints, using the actual route style rather than hypothetical `/v1` names:

- `GET /dali/api/devices` - Return current DALI device state keyed by nice address.
- `GET /dali/api/devices/{address}` - Return state for one DALI device, group, or virtual address.
- `POST /dali/api/devices/{address}` - Set level, color temperature, name, groups, visibility, or other device attributes.
- `GET /dali/api/groups` - Return current DALI group state.
- `POST /dali/api/virtual-groups/{channel}/{group_id}` - Create or update a virtual group and its member devices.
- `GET /dali/api/scenes` - Return configured scenes.
- `POST /dali/api/scenes/{scene_id}` - Create or update scene metadata and saved state.
- `POST /dali/api/scenes/{scene_id}/capture` - Capture current light state into a scene.
- `POST /dali/api/scenes/{scene_id}/trigger` - Trigger a scene locally on the bus and notify trigger listeners.
- `DELETE /dali/api/scenes/{scene_id}` - Delete a configured scene.
- `POST /dali/api/assign-addresses-stream` - Discover unaddressed DALI devices and stream address-assignment progress.
- `POST /dali/api/reassign-address` - Move a DALI device from one short address to another.
- `POST /dali/api/rescan-addresses` - Reconcile the known device list with the physical DALI bus.
- `POST /dali/api/rescan-address/{channel}/{addr}` - Mark one address for delayed rescan/blacklist handling.
- `POST /dali/api/scan-topology` - Stream DR2/topology scan progress and return topology results.
- `POST /dali/api/scan-load-topology` - Stream load-topology scan progress and return results.
- `POST /dali/api/send-raw` - Send raw DALI commands for advanced diagnostics.
- `POST /dali/api/power-status` - Read and return DALI/power supply status.
- `GET /dali/api/server-status` - Return hub health/status for local UI, cron supervision, and support.
- `POST /dali/api/update-server` - Start the local updater flow.
- `POST /dali/api/update-branch/{branch}` - Request an update to a specific release branch.
- `GET /dali/schedule/api/entries` - List schedules.
- `PUT /dali/schedule/api/entries` - Create a schedule entry.
- `POST /dali/schedule/api/entries/{id}` - Update a schedule entry.
- `DELETE /dali/schedule/api/entries/{id}` - Delete a schedule entry.
- `POST /dali/schedule/api/entries/{id}/trigger` - Manually execute a schedule/action entry.
- `POST /dali/cloud/register` - Start cloud preregistration and account-linking flow.
- `POST /dali/wifi/setup` - Configure WiFi.
- `POST /dali/wifi/hot_spot_configure` - Configure hotspot recovery behavior.
- `POST /dali/wifi/offline_mode` - Toggle offline mode behavior.
- `POST /dali/hw-test/run-test` - Run a factory hardware validation test.
- `POST /dali/hw-test/run-fw-test` - Run a firmware regression test.
- `POST /dali/hw-test/cancel-test` - Cancel a running hardware/factory test and clean up safely.
- WebSocket `/log` - Stream DALI/application log events to the UI.
- WebSocket `/devices`, `/groups`, `/broadcast` - Stream device/group/all-state changes to browser clients.

### Data Flow

Local light-control flow:

1. Browser, integration, schedule, trigger, or cloud relay requests a device/group/scene change.
2. Flask route in `serve.py` or a blueprint validates/normalizes the request.
3. `DumbDB` in `dali.py` resolves the address type and updates the live in-memory device model.
4. The command path acquires the DALI lock for the relevant channel/bus.
5. `DALIBus` sends protocol commands through the serial device/custom Pi hat.
6. Durable metadata changes are written to SQLite when needed.
7. Event streams/websockets notify UI clients and trigger listeners of observed state changes.

Installer scan/reconciliation flow:

1. Installer starts address assignment, quick scan, full rescan, or topology scan from the local UI.
2. The API either runs a bounded foreground operation or streams progress through a JSON progress endpoint.
3. DALI commands probe known addresses, unaddressed devices, memory banks, power/topology fields, and device metadata.
4. `DumbDB` merges physical observations with SQLite configuration and last-known state.
5. Missing/MIA devices are marked so installers can see devices that used to exist at an address but no longer respond.
6. Background monitor work continues lower-priority reconciliation after the immediate installer task completes.

Startup/recovery flow:

1. systemd starts the release loader and application runtime.
2. Alembic migrations apply to the on-device SQLite database.
3. ZPDS initializes DALI, DB-backed configuration, integrations, schedule threads, cloud listener, websockets, and watchdogs.
4. Quick restore loads recent known light state so the UI is useful before a full bus scan completes.
5. The DALI monitor reconciles the restored model with physical bus observations and missing-device detection.

Cloud command flow:

1. The hub registers with the management server and stores an API token in local site settings.
2. The hub reports visible device state to the cloud.
3. The hub opens an outbound listen stream to the cloud message endpoint, avoiding inbound NAT/router setup.
4. Cloud messages such as `set-state` are translated into local `set_dali_light_state` calls.
5. The cloud listener reconnects after errors and is supervised by a watchdog.

Factory validation flow:

1. Factory user opens the hardware test page for a switch, DR2, or related control unit.
2. The test runner powers devices through GPIO-controlled relays and executes DALI reads/writes or firmware regression scripts.
3. Progress and errors are streamed to the browser.
4. Voltage/current/wattage and memory/firmware checks determine pass/fail.
5. Cancellation and exception paths turn power off in cleanup so failed tests do not leave units energized.

### High-Level Design

- Local web/admin UI: browser-facing pages for installers, customers, support, hardware debugging, factory test workflows, and advanced operations.
- API service: Flask/gunicorn application exposing REST-style JSON routes, streaming progress routes, Jinja pages, and websocket event routes.
- Live device-state coordinator: in-memory model that reconciles API writes, DALI observations, scan results, quick restore snapshots, and DB metadata.
- Hardware bus adapter: serialized DALI command path over a custom Pi hat/serial interface, plus raw command and topology/power diagnostic tooling.
- Persistent configuration store: local SQLite database with Alembic migrations for durable settings, device metadata, groups, scenes, schedules, integrations, and quick restore snapshots.
- Scheduler and trigger engine: background thread system for time schedules, sunrise/sunset, DALI events, Z-Wave/DMX/WiZ events, scenes, macros, delayed actions, heartbeats, and rolling scans.
- Integration adapters: Hue-compatible local API, WiZ LAN control, Z-Wave event handling, DMX frame ingestion, Control4 SDDP discovery, and cloud/Google/Alexa support.
- Cloud/support relay: outbound registration, device reporting, command listening, status/backup/update hooks, and release metadata reporting.
- Provisioning and recovery supervisor: WiFi setup, hotspot/offline mode, cron-based health checks, backups, update requests, service restarts, and network recovery behavior.
- Factory/hardware validation tool: hardware test UI and runner for pre-shipment validation of switches, DR2s, and firmware behavior.

### Data Models

Device:

- Table: `devices`
- Relevant fields: `channel_id`, `dali_addr_short`, `serial_number`, `upc_code`, `name`, `hue_name`, `hue_hidden`, color-temperature bounds, physical/user calibration ranges, `rgb_power_scale`, `wattage_rating`, `addtl_fields`.
- Purpose: durable metadata for physical DALI lights/control devices; live level/state is reconciled in memory from the bus.

PassiveDevice:

- Table: `passive_devices`
- Relevant fields: `channel_id`, `dali_addr_short`, `name`.
- Purpose: represent known DALI devices that are not normal active light outputs.

Group:

- Table: `groups`
- Relevant fields: `channel_id`, `group_id`, `name`, `hue_hidden`.
- Purpose: persistent naming/visibility for DALI group addresses.

VirtualDevice:

- Table: `virtual_devices`
- Relevant fields: `channel_id`, `dali_addr_short`, `groups`, `name`, `n_dmx_devices`, `hue_hidden`.
- Purpose: represent software-visible devices layered on top of physical DALI behavior, often for DMX or integration compatibility.

VirtualGroup:

- Table: `virtual_groups`
- Relevant fields: `channel_id`, `group_id`, `devices`, `name`, `level`, `hue_hidden`.
- Purpose: persist software-managed group membership and visible group state.

Scene:

- Table: `scenes`
- Relevant fields: `name`, `visible`, `state`.
- Purpose: saved lighting state snapshots for later trigger/control.

ScheduleEntry:

- Table: `schedule_entries`
- Relevant fields: `name`, `conditions`, `actions`, `last_triggered`.
- Purpose: flexible JSON-backed model for time schedules, triggers, macros, delayed actions, and integration events.

SiteSettings:

- Table: `site_settings`
- Relevant fields: `site_name`, `send_power_on_level`, `manage_failover`, `failover_level`, `heartbeat_interval`, `enable_backups`, `dataplicity_key`, `api_token`, `power_supply_count`, `addtl_settings`.
- Purpose: hub-level configuration shared by ZPDS, support tooling, cron supervision, backups, and cloud registration.

QuickRestore:

- Table: `quick_restore`
- Relevant fields: `id`, `light_state`.
- Purpose: persist recent last-known light-state snapshots for fast startup recovery before a full bus scan completes.

DeviceBank5Data:

- Table: `device_bank_5_data`
- Relevant fields: `upc_code`, `fw_version`, `hw_version`, `serial_nb`, `bank_5_data`.
- Purpose: store device memory dumps used for hardware/firmware diagnostics and compatibility validation.

Integration models:

- `zwave_nodes`: `home_id`, `node_id`, `name`, `hidden`, `data`.
- `wiz_bulb`: `mac`, `name`, `ip`.
- `rooms`: `name`, `wiz_bulbs`.
- `dmx_assignments`: `entity_type`, `entity_id`, `address_start`, `address_end`.
- Hue compatibility tables: users, lights, groups, scenes, and config for Hue/Home Assistant-style clients.

### Component Descriptions

Local web/admin UI:

- Provides the human operating surface: basic light control, device pages, groups, scenes, schedule/triggers, topology, raw DALI commands, WiFi setup, support/admin pages, hardware test pages, and advanced Murray/hardware debug screens.

API service:

- Owns request validation, route composition, page rendering, progress streams, websocket routing, and boot-time component initialization.
- In ZPDS this is centered in `serve.py` with additional blueprints for schedule, cloud, WiFi, Z-Wave, and hardware testing.

Live device-state coordinator:

- Bridges three truths: durable config in SQLite, physical state on the DALI bus, and requested state from users/integrations.
- In ZPDS this role is mostly `DumbDB`, which is a misleading name but is the live reconciliation layer.

Hardware bus adapter:

- Owns DALI wire-protocol details, retries, response parsing, raw commands, topology reads, power-status reads, and command serialization.
- The bus lock and low-priority monitor behavior protect the single shared protocol resource.

Persistent configuration store:

- SQLite persists the small but important per-house state: settings, metadata, schedules, scenes, groups, integration mappings, and quick restore snapshots.
- Alembic migrations let deployed hubs move forward without manual database repair.

Scheduler and trigger engine:

- Converts time, sunrise/sunset, DALI events, Z-Wave events, DMX frames, scenes, and macros into actions.
- Uses explicit locking to avoid recursive trigger and DALI command deadlocks.

Integration adapters:

- Let the hub participate in external ecosystems without making cloud availability a prerequisite for local control.
- Includes Hue/Home Assistant-style behavior, WiZ, Z-Wave, DMX, Control4 discovery, Google/cloud paths, and Alexa/cloud-side surfaces.

Cloud/support relay:

- Uses outbound connections for registration, device reporting, remote commands, update/control metadata, and support visibility.
- Avoids requiring customers/installers to configure inbound router access.

Provisioning and recovery supervisor:

- Handles product operations that live below the web app: WiFi/hotspot/offline recovery, backups, service health checks, update requests, and network status.

Factory/hardware validation tool:

- Gives manufacturing and hardware engineering a repeatable way to validate control units before shipment.
- Encodes power sequencing, DALI memory behavior, firmware regression execution, and safe cancellation/cleanup.

### Deep Dives

DALI serialization and bus contention:

- The central bottleneck is not the Flask API or SQLite. It is the physical DALI bus.
- User actions, schedule actions, scans, topology reads, monitor reads, and raw commands all contend for a stateful serial protocol.
- The design uses DALI locks, low-priority monitor behavior, timeouts, and watchdogs to keep background work from blocking foreground control indefinitely.
- Incident angle: high-device-count broadcast plus macros exposed lock-order and deadlock problems, which led to stronger lock discipline and watchdog behavior.

Scanning, quick restore, and missing-device recovery:

- Full bus scans are too slow and disruptive to run synchronously on every page load or startup.
- ZPDS uses durable metadata plus quick restore snapshots to make the UI useful quickly, then reconciles with the physical bus.
- Missing-device tracking matters because installers need to know that a device used to exist at a DALI address but no longer responds.
- Incident angle: quick restore initially created DB-write pressure and recovery-scan bugs, which forced better separation between fast restore, save cadence, and targeted MIA rescans.

Installer workflow:

- The installer experience drove a large part of the system shape.
- The hub had to work from a laptop or iPhone on site, sometimes with unreliable internet and awkward customer WiFi.
- DALI was a good topology choice because the software generally cared about bus/address membership rather than the exact physical placement of every device, except in multi-bus house designs.
- The local admin UI, scans, missing-device display, raw command tools, hotspot/offline mode, and topology pages were built around reducing installation friction.

Field reliability:

- Hubs are deployed in homes, so failure recovery cannot assume an engineer is present.
- Reliability is layered: systemd restarts, `cron-boom` health checks, app-level watchdogs, DALI/schedule heartbeats, cloud listener reconnects, quick restore, backups, and hotspot/offline paths.
- This is a principal-engineer point: the system is not just app code; it is an appliance-like product installed in uncontrolled networks.

Cloud command delivery:

- Outbound cloud listening was the right connectivity trade-off because customer homes rarely have inbound routing configured.
- The trade-off is that command freshness, reconnect behavior, cached device state, and message durability become explicit concerns.
- In an interview, frame this as a pragmatic NAT/firewall decision rather than as a generic cloud architecture.

Release/update model:

- The release loader, encrypted payload, ramdisk startup, branch/tag metadata, and updater service solved real field needs: centrally controlled releases, IP/source protection, and remote recovery.
- The downside is operational complexity on top of Raspberry Pi OS and Python packaging.
- This connects directly to the hindsight section: for a long-lived field appliance, a more standard signed/rollback image model or more controlled embedded Linux base may have been cleaner.

Factory validation:

- Factory testing turned hardware/firmware requirements into a repeatable software workflow before units shipped to the United States.
- The important system-design requirement is safety and repeatability: tests need progress reporting, clear failure modes, DALI memory/firmware checks, and cleanup paths that power devices down even after cancellation or errors.
