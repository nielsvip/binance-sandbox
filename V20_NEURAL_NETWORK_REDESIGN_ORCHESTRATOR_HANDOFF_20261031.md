# V20 neural-network redesign: orchestrator instructions and evidence handoff

**Primary task: redesign and apply the v20 neural network using the complete collected backtest evidence.**
**Immediate task: actual coverage of EVERY row × EVERY filter column on all four servers.**
**Authority threshold: more than 100 proven results for a cell means at least 101; never substitute predictions or duplicated records.**
**SPAWN CHEAP AGENTS, MANAGE THEM, AND DO NOT LOSE FOCUS ON V20.**

This is an operational handoff, not a completion certificate.
The user explicitly requested at least 3,000 lines of instructions.
The length comes from operational instructions, a category/tab work matrix, ingestion work packets, and a source evidence index.
No appended source index entry means the associated task has passed.
No checklist item is automatically satisfied merely because its file exists.
Read the urgent campaign section before the historical research sections.
Use the current workspace and actual process state to refresh anything that has changed.
The final section records the document's construction and validation scope.

### Absolute focus

Keep the complete v20 redesign objective intact across continuations and compaction.
Do not replace it with storage maintenance, a tiny canary, a standalone helper, or an inventory report.
Those can be prerequisites; they are not the requested end state.
Use agents for parallel execution and keep the root agent responsible for integration and verification.
Prefer cheap agents for inventories, parsing, provenance checks, and bounded implementation work.
Use a stronger agent only where the task complexity warrants it.
Do not spawn agents merely to restate the plan.
Give each agent concrete files, a bounded responsibility, a deliverable, and a validation requirement.
Keep ownership of shared files explicit so agents do not overwrite each other's work.
Never interrupt or kill agents just to write or deliver this handoff.
The user specifically asked that agents continue while the handoff is written.

### What success still requires

Use the information collected over two months of continuous computation on four servers.
Read the existing 5 TB Hetzner archive and the active source stores.
Reconcile actual trainer consumption, not merely storage copies.
Calculate every row/filter-column relationship, including newly added rows and cells.
Retain positive, negative, zero, invalid, and unknown distinctions honestly.
Permit authoritative promotion or demotion only after the required proven-result threshold.
Preserve the accepted starting baseline on identical inputs.
Respect the required blanket-off/kindergarten phase before the ordinary first row pass.
Keep the first row pass worst-first according to the actual template writer's ordering rule.
Continue pinpoint interaction testing after that first pass.
Prioritize profit protection and recovery relationships without inventing profitable fills.
Run the S2 champion-versus-candidate gate for genuine neural-network improvement.
Only then prove at least 90% less compute per sym_side without worse results from skipped cells.
Enable the intended 60D testing and daily catch_up runs on S1/S2.
Do not retire S5/S6 until evidence, capacity, and execution readiness are actually verified.

### Latest urgent direction takes precedence over earlier sampling plans

The latest user said every row needs a complete run of backtests, even if partial, of every filter column.
The coverage campaign must not limit itself to currently yellow-painted cells.
A low historical pos_sym is not permission to omit a cell from this campaign.
A new row is not allowed to inherit a dead or untested classification from old wiring.
A model prediction is not a proven result.
An inherited baseline value is not a new measured cell result.
A failed evaluation is not a zero delta.
A setting that was silently discarded is not a proven ineffective filter.
A partial campaign remains visibly partial.
The one-to-two-hour deadline applies to making measurable coverage progress with all four servers.
Do not invent a claim that every cell has reached 101 results if the measured throughput cannot support it.
Report actual remaining work and measured completion rates.

### Current time window for the urgent campaign

The root recorded the urgent campaign start as 2026-10-31T04:28:19Z.
The first requested deadline is 2026-10-31T05:28:19Z.
The outer requested deadline is 2026-10-31T06:28:19Z.
These are the user's campaign target times, not a guarantee of completion.
If reading after those times, inspect actual output and report the real outcome.
Do not silently reset the deadline because this handoff was created later.
The machine-readable contract is data/reports/full_cell_campaign_20261031/operator_contract.json.

### Do not repeat the previous drift

The supervisor spent too much time on local storage preservation and isolated review helpers.
The user corrected that drift explicitly.
The purchased Hetzner 5T archive keeps everything and is topped up.
Archival capacity must not be presented as the main obstacle to using the evidence.
Local RAM, temporary disk, and live-process requirements still need operational management.
Manage those as bounded staging concerns, not an excuse to abandon full ingestion.
A full source inventory alone does not mean the neural network has absorbed the data.
A retention catalog alone does not mean prior weights were continued.
A successful small backtest alone does not prove the full network or the fleet is ready.
A source hash proves bytes, not trading correctness.
A launched process proves an attempt, not completed coverage.
A helper unit test proves its tested behavior, not market gain.

### Repository and execution location

Workspace: /Users/niels/Documents/binance.
The shell may display the case-normalized path /users/niels/documents/binance.
Main current worker aliases: s1-int, s2, s5, s6.
Check ssh -G and remote hostname before using a literal private address.
An address mistake was caught: 10.0.0.2 resolved to the gateway, not S2.
The actual s2 alias resolved to 10.0.0.4 during the latest transfer.
That failed preflight exited before creating or copying a runtime on the gateway.
Do not repeat guessed-address deployment.
Use configured aliases and verify host identity.
Production changes require the actual applicable S2 gate and file-lock discipline.
Isolated research staging is not production deployment.

### User-facing communication

Lead with completed work and the next concrete action.
Report uncertainty and failures candidly.
Avoid claiming a test passed merely because a supervisor returned exit code zero.
Inspect the actual worker status and its result receipt too.
Do not overwhelm the user with repeated plans while nothing is executing.
Do not repeatedly ask for permission already given.
When a real lock or approval rule applies, prepare the concrete reviewable patch first.
Explain the exact rule rather than creating a vague safety objection.
Never use this handoff as authority to bypass higher-priority instructions or tool limitations.
## 1. Original user request, preserved verbatim
> We have constanly running backtests for vectorized stock and crypto trading that record all their settings and calculations which we use for building a neural network between all the 3000+ switches and filters. Filters are in the 'yellow boxes' on the template workbooks and the system has to start understanding what filters can have a pos delta on what switches and give these priority in testing. Every day we calculate avg deltas per switch and pos_sym (the number of pos deltas the switch has ever had). Test frequency of low pos_sym gets reduced every time but we added a lot of new switches that previously has been ignored from the trading scripts. Can you supervise the build of the next generation neural network that has to expand exponentially by using ALL the data we have collected on 4 servers and 4 external drives, absorbing it into the neural network and compacting it where possible. You need to be the supervisor here spawning cheaper agents for each job depending on its complexity. The 2 goals are augmenting gain drastically without faking numbers, and decreasing compute time per sym_side by at least 90% so the 60D test can run this weekend, after which s5 and s6 will be terminated and all compute needs to run on s1 and s2 with daily reruns for all sym_sides on the catch_up setting in v15_pilot/v20/21/22
## 2. Subsequent user instructions, preserved verbatim
### User correction 1

> for the gain improvement we run the s2 gate that has to be used in order to promote to a new neural network version by comparing with the last; we can do that first and the speed improvement later by rerunning the new version on old numbers and making sure the results dont deteriorate by the cells we skip
### User correction 2

> it always has to start from the previous baseline (on identical npz it should not change any new switches need to be off by default or in same setting as before adding)
### User correction 3

> keep going with everything just one remark ENABLE_TR_SWING_ENTRIES<- careful with that one it was taken out multilple times as it stopped all backtests so verify it now works or delete it completely from all code
### User correction 4

> ok tr_swin was a tiny detail concentrate on what you are doing sorry for the interruption when done focus on rolling out the complete neural network where every connection between a switch and a filter, a filter and a filter, a switch and any other switch is verified and used in future tests
### User correction 5

> ok befor doing the s2 comparison I want you to make sure this is addressed correctly as the main source of losses are avoidable fixed stops (need to exist but should be avoided if there was a gain ever - then reentered immediattely on recover) : I still seem to be witnessing good gains turning into losses without closing in time then closing at an abysmal stoch_low/high_15m break so I think the main problem in the backtests is they are not protecting positions sufficiently (usdc maker is commission free so should never stay into a loss if it can be avoided. Make sure the neural network has priorities to avoid this behavior (same for stocks that have no commissions at all)
### User correction 6

> won’t bypass that gate merely to force an entry <- it is very simple whatever the switch says (pos delta in the backtest for that sym_side) is the setting you aplly. Everywhere. No exceptions. NO config or script setting has ANY power if a switch exists
### User correction 7

> there is a EMA_BLANKET setting that we are phasing out by testing it as soon as the new baseline for a starting test is established - ema_blanket is switched fof and the bet kindergaten settings are kept (in case you are refrring to this) Then the row by row worst first test (the first round) starts, after which ethe pinpoint settigns (that you are owking on) continue; I think we neeed to experiment many new yellow cells on the newly added rows but we do still need to save a lot of time per sym_side (which should be easy jusdging by the number of 0 results I see in the final templates)
### User correction 8

> No intention of interruption keep going you are the main orchestrator yoou can not let your guard down if you have any doublts let me know but spawn agents and keep working no matter what
### User correction 9

> we do NOT have any storage problem anymore we bought a 5T hetzner drive that keeps EVERYTHING and gets topped up - the space probelm is gone but you need to use avery byte of information collected in 2 months of 4 servers computeing backtest 24/7 and apply it
### User correction 10

> GET BACK ON TRACK --- EVERY row has to have a complete run of backtests (even if partial) of EVERY filter column so it is clear which ones are useful and which ones are not. When you have >100 proven results you can promote or demote any filter column cell with authority even the new ones. USe all compute on 4 servers to figure this out within an hour or two. STP losing track of your job
### User correction 11

> please write an at least 3000 line instruction md containing initial prompt and advances and next steps to take; do not interrupt agents if any but make very clear that it needs to spawn cheap agents and manage them and NOT LOSW FOCUS on the v20 redesign of the neural netwrok
## 3. Immediate next actions: execute, do not restart the investigation
- Read the latest full-cell campaign catalog and runner-stage files before creating another runner.
- Check all three existing agents; do not spawn duplicate ownership of their files.
- Have learning_audit finish the all-row/all-column evaluator and give compute_audit its exact immutable source hash.
- Have compute_audit start the first S6 batch immediately when the runner is verified.
- Start the corresponding isolated batches on S1/S2/S5 using the already materialized common runtime.
- Ramp worker counts using actual CPU/RAM measurements, not load averages alone.
- Use the existing evidence index to credit only independently verified cell observations.
- Write attempted, measured, informative, invalid, not-applied, and unattempted counters separately.
- Give every catalog cell a first actual attempt before concentrating all new compute on the first few rows.
- Use the more-than-100 threshold only for authoritative promotion/demotion; do not use it as an excuse to omit low-count cells.
- Keep all campaign results linked to their exact template, engine, accepted settings, and NPZ context.
- Feed measured relationships into v20 training and test selection; do not stop at producing a spreadsheet.
- Preserve every running process identity and poll it before any restart.
- At the deadline, publish actual coverage and unresolved cells even if the campaign is incomplete.
- Continue the complete redesign after the urgent campaign; do not silently shrink the long-term objective.
## 4. Agent assignments and cheap-agent management
### Root orchestrator
- Own the v20 objective, integration, launch approval within authorization, deadline, and evidence quality.
- Do not become a serial file-inventory worker while agents are idle.
- Use a cheap capable agent for this bounded responsibility unless the evidence shows it needs a stronger model.
- Assign explicit file ownership before edits.
- Return concrete source hashes and paths with each handoff.
- Return actual commands or runnable artifacts, not only recommendations.
- State which validation actually ran.
- State what was not tested.
- Preserve an active job when observation times out.
- Send a milestone when execution starts or finishes.
- Do not count the same result twice because two agents observed it.
- Escalate a real ambiguity briefly while continuing independent work.
### compute_audit
- Own fleet capacity, runtime materialization, actual execution, and switch-authority validation.
- Do not keep adding isolated code reviews while the coverage campaign is waiting for execution.
- Use a cheap capable agent for this bounded responsibility unless the evidence shows it needs a stronger model.
- Assign explicit file ownership before edits.
- Return concrete source hashes and paths with each handoff.
- Return actual commands or runnable artifacts, not only recommendations.
- State which validation actually ran.
- State what was not tested.
- Preserve an active job when observation times out.
- Send a milestone when execution starts or finishes.
- Do not count the same result twice because two agents observed it.
- Escalate a real ambiguity briefly while continuing independent work.
### learning_audit
- Own the full Cartesian catalog, actual evaluator, count ledger, evidence ingestion, and v20 consumer.
- Do not replace actual ingestion with another proposal-only helper.
- Use a cheap capable agent for this bounded responsibility unless the evidence shows it needs a stronger model.
- Assign explicit file ownership before edits.
- Return concrete source hashes and paths with each handoff.
- Return actual commands or runnable artifacts, not only recommendations.
- State which validation actually ran.
- State what was not tested.
- Preserve an active job when observation times out.
- Send a milestone when execution starts or finishes.
- Do not count the same result twice because two agents observed it.
- Escalate a real ambiguity briefly while continuing independent work.
### corpus_inventory
- Own fast recovery of existing proven cell evidence from active stores and 5T.
- Do not make new local copies the primary output when archived indexed evidence is available.
- Use a cheap capable agent for this bounded responsibility unless the evidence shows it needs a stronger model.
- Assign explicit file ownership before edits.
- Return concrete source hashes and paths with each handoff.
- Return actual commands or runnable artifacts, not only recommendations.
- State which validation actually ran.
- State what was not tested.
- Preserve an active job when observation times out.
- Send a milestone when execution starts or finishes.
- Do not count the same result twice because two agents observed it.
- Escalate a real ambiguity briefly while continuing independent work.
## 5. Current verified advances and strict limits
### Initial baseline reproduction
- Verified advance: The 1INCHUSDT_SHORT candidate initial baseline matched the prior runtime on four actual hashes.
- Limit: This was one side, not full-universe parity or neural-network gain.
- Next action: inspect the linked evidence index and current state before extending this claim.
### Baseline guard and resume
- Verified advance: A local pilot forward patch and separate guard/resume helpers were applied and tested locally.
- Limit: The canonical local pilot hash recorded below is not a fleet-deployment claim.
- Next action: inspect the linked evidence index and current state before extending this claim.
### Template-layer authority repair
- Verified advance: The isolated crypto case restored 36 selected values that had been overwritten; IBM restored 28.
- Limit: These are code-authority changes, not improvements attributable to the neural network.
- Next action: inspect the linked evidence index and current state before extending this claim.
### Orphan EMA20 veto
- Verified advance: A separate hardcoded EMA20 reentry veto changed the actual ledger despite unchanged selected settings.
- Limit: Removing it in a review candidate does not establish universal live/vector authority.
- Next action: inspect the linked evidence index and current state before extending this claim.
### Independent code-repair controls
- Verified advance: Layer-only, orphan-only, and combined crypto arms completed on frozen inputs; IBM combined completed too.
- Limit: The old oracle records remain intact; corrected behavior is a separately labeled era.
- Next action: inspect the linked evidence index and current state before extending this claim.
### Historical protection quartets
- Verified advance: Three actual four-component experiments completed on the historical runtime.
- Limit: No joint winner was adopted.
- Next action: inspect the linked evidence index and current state before extending this claim.
### Corrected protection quartets
- Verified advance: Three corrected-runtime quartets completed with pure effective-field factorial checks.
- Limit: A conditional filter benefit remained below the total baseline and therefore did not justify adoption.
- Next action: inspect the linked evidence index and current state before extending this claim.
### Two missing real fields
- Verified advance: FIXED_QUANTITY_ENABLED and WT_MOMENTUM_EXIT_ENABLED were added as real False-default dataclass fields in a separate review candidate.
- Limit: This candidate was not copied over newer canonical engine work.
- Next action: inspect the linked evidence index and current state before extending this claim.
### Two-field False controls
- Verified advance: Crypto and IBM controls preserved prior-config projection, ledger, deterministic result, and cutoff.
- Limit: Neutrality is proven for the tested cases, not every possible market context.
- Next action: inspect the linked evidence index and current state before extending this claim.
### Six True variants
- Verified advance: All six version-two field diagnostics completed with independent prepared copies and array-mutation checks.
- Limit: Observed native and ledger effects are not a gain-improvement claim.
- Next action: inspect the linked evidence index and current state before extending this claim.
### Live fixed-quantity review
- Verified advance: An isolated ez_manage patch removes manager/global OR precedence in favor of explicit per-side lookup.
- Limit: It is review-only, with no live order calls or canonical deployment.
- Next action: inspect the linked evidence index and current state before extending this claim.
### Actual fork proof producer
- Verified advance: The fork-worker smoke reproduced all four baseline hashes and captured actual native evidence.
- Limit: This is not a complete first row pass or complete factory integration proof.
- Next action: inspect the linked evidence index and current state before extending this claim.
### Source-bound exploration
- Verified advance: The allocator rejects stale booleans as current measured coverage and protects new-row exploration.
- Limit: Its review implementation still needs full campaign integration and actual coverage.
- Next action: inspect the linked evidence index and current state before extending this claim.
### Consumer adapter
- Verified advance: Actual pilot callsites, worker capture, and chain-aware row receipts were implemented in a composed review copy.
- Limit: Unit/extracted-callsite tests do not establish fleet activation.
- Next action: inspect the linked evidence index and current state before extending this claim.
### Template order diagnosis
- Verified advance: The actual writer intentionally hoists the first white default-containing group, explaining a prior validator mismatch.
- Limit: A physical ordering match alone is not proof of the historical producer invocation.
- Next action: inspect the linked evidence index and current state before extending this claim.
### Expanded training run05
- Verified advance: All eight phases finished on the expanded but still limited corpus.
- Limit: Fresh initialization and 202 emitting sides are not absorption of all two-month evidence.
- Next action: inspect the linked evidence index and current state before extending this claim.
### Prior knowledge retention
- Verified advance: A source-bound catalog preserves prior graph and prediction information for future test selection.
- Limit: A catalog is not exact optimizer continuation or all-raw-data training.
- Next action: inspect the linked evidence index and current state before extending this claim.
### Lossless sort proof
- Verified advance: The streaming-sort proof matched the full original sorted byte output.
- Limit: It was not adopted in run05 and is not a backtest speed benchmark.
- Next action: inspect the linked evidence index and current state before extending this claim.
### 5T archive discovery
- Verified advance: All four server roots plus Mac, WD25, and TOSHIBA roots were observed on the actual archive.
- Limit: Archive presence does not prove extraction or trainer consumption.
- Next action: inspect the linked evidence index and current state before extending this claim.
### Indexed historical evidence
- Verified advance: Large lifecycle progress files expose exact per-row yellows and skip/error metadata.
- Limit: Raw row/cell counts are not automatically eligible proven-result counts.
- Next action: inspect the linked evidence index and current state before extending this claim.
### Fleet runtime staging
- Verified advance: S1/S2/S5 copies of the common immutable runtime were hash-verified; S6 has the original.
- Limit: At the last verified handoff point this proved readiness, not launched full-cell workers.
- Next action: inspect the linked evidence index and current state before extending this claim.
### Storage preservation
- Verified advance: Several S5 and S6 evidence components were preserved and verified before the focus correction.
- Limit: These copies must not distract from applying the archived information to v20.
- Next action: inspect the linked evidence index and current state before extending this claim.
## 6. Baseline, phase order, and authority rules
- Start every controlled comparison from the previous accepted settings for that exact sym_side.
- Freeze the NPZ and record its actual bytes and prepared cutoff.
- For a new data day, compute the prior accepted runtime on the new frozen input before comparing the candidate on those same bytes.
- Do not recycle an old baseline pin across changed NPZ data.
- A neutral new switch starts OFF or reproduces its previous fallback behavior.
- Keep code-repair experiments separate from neural-network experiments.
- Compare old and new neural networks on a common explicitly chosen runtime.
- Do not count a newly repaired override path as neural-network gain.
- The required phase order is accepted baseline, blanket-off/kindergarten, first worst-first pass, then pinpoint research.
- EMA_BLANKET_FILTER_ENABLED=False disables the blanket; setting its filter TF to OFF is not equivalent.
- Test all actual offered kindergarten single options; a time budget may bound a pairwise tail, not silently omit required singles.
- Persist the measured kindergarten winner before advancing to the first row pass.
- Resume must retain that winner and verify its input and chain identity.
- A failed kindergarten probe cannot be labeled complete.
- The latest failed probe was a harness contract error: it assumed sanitized_floor existed in the evaluator output.
- The real evaluator returned a Python True validity value and no sanitized_floor field.
- Do not weaken validity rules to hide this probe error; pass the actual canonical floor through the correct interface.
- The urgent coverage diagnostic may run from a declared accepted baseline without pretending it is a completed production KG/row workflow.
- No independent config or hardcoded script veto may silently override a selected switch.
- Keep required exchange/order execution routing intact while correcting strategy-setting precedence.
- Retired MIN_HOLD aliases must migrate to canonical MIN_HOLD_BARS rather than being resurrected as competing thresholds.
- Keep the mandatory fixed loss stop intact.
- Tunable per-symbol profit protection must not weaken that mandatory stop.
- Use actual applicable commissions, spread, and slippage assumptions; zero commission is not zero execution friction.
- A prior positive price excursion does not by itself prove an executable profitable exit existed.
- An entry signal cleared by an unselected gate is an authority issue; document the actual code path.
- Unknown or missing input arrays must remain explicit; never substitute another timeframe silently and call it the selected setting.
- The current scope still has unresolved mappings and live/vector differences; do not assert universal authority.
## 7. Fleet operating facts at the latest inspected checkpoint
- Four workers were observed with 16 vCPUs each.
- One-second CPU samples showed meaningful idle capacity despite high load averages.
- Initial campaign worker allocation was approved as S1=2, S2=4, S5=2, S6=4.
- These are starting counts; ramp them against actual CPU, RAM, and live-service health.
- Do not treat twelve launched workers as proof of twelve effective CPU cores of useful work.
- Record actual completed cell rates and parent-plus-child CPU usage.
- Live execution, position truth, NPZ tail, and state production must continue.
- Ordinary backtests may be reprioritized through documented reversible controls when necessary.
- Preserve existing progress before any such change.
- Do not use arbitrary SIGSTOP on shared process groups.
- Do not hijack /tmp/v15_preopen_7d.pause; it belongs to active account-specific work.
- The existing fleet_drain_hosts.json admission mechanism has no built-in expiry.
- Any temporary lease using it needs explicit ownership, preserved prior entries, and a robust expiry guardian.
- Do not spend the campaign hour engineering a pause mechanism if enough idle capacity already exists.
- The common measured runtime contains 4,366 files totaling 946,168,793 bytes.
- The common reference path is /home/niels/v15_bench/reviews/authority_arms_20261031_review1/both_reference.
- S1/S2/S5 received isolated copies; production folders were not overwritten.
- S6 retains the original measured reference.
- The initial four frozen NPZ files do not establish a complete four-category test population.
- Crypto LONG requires independently accepted LONG settings; do not reuse a SHORT recipe under a LONG label.
- Use a source-bound accepted baseline for every additional symbol side.
- An older runtime may ignore newly added fields; classify those as not applied, not proven zero.
- Coverage must explicitly expose that limitation until the appropriate wiring is tested.
- The 20 GB official gate floor is not the same resource contract as a small isolated diagnostic batch.
- Do not weaken the production gate to make a research batch fit.
- Use bounded output chunks and the existing 5T archive for campaign retention.
- Do not claim the storage problem still prevents ingestion simply because a worker has limited staging disk.
## 8. 5T and full-corpus ingestion
- Use the configured 5t alias; do not print or copy private keys.
- The correct storage account is u706004 on port 23.
- The storage service supports SFTP/rsync transfer operations; do not assume a general remote command shell.
- The old offload_to_storagebox.sh references an obsolete different account and is not the authoritative 5T layout.
- Observed server roots are /home/s1/binance-sandbox, /home/s2/binance-sandbox, /home/s5/binance-sandbox, and /home/s6/binance-sandbox.
- Observed additional roots are /home/mac, /home/WD25, and /home/TOSHIBA_EXT.
- SSD2T and E3T roots were not observed in the bounded top-level listing; that is not proof their data is absent everywhere.
- The urgent campaign uses indexed cell evidence first; preserve the full archive traversal checkpoint for later continuation.
- Progress JSON files under lifecycle_pilot are a concrete high-value evidence source.
- Observed archived progress counts were about 1,000 on S1, 991 on S5, 986 on S6, and 8 on S2 in the inspected location.
- Those are observed file counts, not confirmed non-overlapping boards or completed tests.
- One inspected UNIUSDC_LONG progress file exposed 3,248 done rows and 55,793 raw yellow results.
- That sample does not establish eligibility or coverage of the entire archive.
- Parse reason, complete, type_skipped, excluded_unwired, tab_level_excluded, sampled_out_filters, zero_skipped_filters, and yellow_dups.
- Never turn these status fields into synthetic measured zeros.
- Use exact per-row calculation timestamps where available; file mtime is not a computation date.
- Use actual row NPZ identity and chain state where available.
- Recover missing identity from archived delta logs, settings histories, and NPZ manifests.
- Keep unknown identity explicitly unresolved rather than fabricating it.
- Deduplicate replicas without adding their support counts together.
- Keep conflicting records with the same logical identity visible for investigation.
- Credit qualifying historical results toward the 101-result threshold only after the cell and context are established.
- Unknown old wiring cannot authoritatively demote a newly wired field.
- For full training, record source SHA, extraction counts, emitted shard identities, and actual trainer consumption.
- An input manifest alone is not a consumption receipt.
- Research final-fit data and pre-holdout-qualified data need separate explicit roles.
- Legacy predictions with unproven market lineage cannot become independent holdout evidence.
- Do not set an arbitrary twelve-board cap in the explicit full-corpus mode.
- The earlier gain_population default cap was a bounded research mechanism, not the full objective.
- Keep useful legacy context as historical or auxiliary information when it cannot supply a verified gain label.
- Do not count a source as applied merely because it was archived or listed.
- Do not claim a fresh model retained old learned knowledge unless the actual training path proves it.
- CatBrain training currently constructs fresh objects; --state is an output artifact in the inspected path.
- LinkNet saved parameters do not include the old optimizer moments in the inspected implementation.
- Never describe a weights-only restart as exact optimizer continuation.
- Keep old model vocabularies and context vocabularies bound to their saved weights.
- Prior scores may help rank future research only under their declared provenance and cutoff rules.
- Old scores must not exclude unknown/new cells from this urgent exhaustive campaign.
## 9. Evidence counting and the 101-result rule
### Catalogued
- A row/filter-option target exists in the frozen campaign catalog.
- This is a target, not a calculation.
- Record this status with the exact cell identifier.
- Record the source engine and template identity.
- Record the actual window and accepted-chain context.
- Record the original calculation date when established.
- Keep status changes append-only or versioned.
- Expose this status in campaign coverage reports.
### Attempted
- The campaign dispatched a concrete request for that target and context.
- A dispatch or worker PID is not a result.
- Record this status with the exact cell identifier.
- Record the source engine and template identity.
- Record the actual window and accepted-chain context.
- Record the original calculation date when established.
- Keep status changes append-only or versioned.
- Expose this status in campaign coverage reports.
### Measured
- The real engine returned a traceable completed evaluation.
- Keep invalid-performance and no-exposure cases separately labeled.
- Record this status with the exact cell identifier.
- Record the source engine and template identity.
- Record the actual window and accepted-chain context.
- Record the original calculation date when established.
- Keep status changes append-only or versioned.
- Expose this status in campaign coverage reports.
### Informative
- The evaluation had the required applied setting and meaningful context for the claimed conclusion.
- Do not assume a zero-trade window proves a filter is generally useless.
- Record this status with the exact cell identifier.
- Record the source engine and template identity.
- Record the actual window and accepted-chain context.
- Record the original calculation date when established.
- Keep status changes append-only or versioned.
- Expose this status in campaign coverage reports.
### Invalid
- The actual engine rejected the candidate or its performance constraints.
- Do not convert an invalid result into zero delta or a positive winner.
- Record this status with the exact cell identifier.
- Record the source engine and template identity.
- Record the actual window and accepted-chain context.
- Record the original calculation date when established.
- Keep status changes append-only or versioned.
- Expose this status in campaign coverage reports.
### Not applied
- The requested setting failed to reach the effective configuration or had no implemented path.
- Do not count silent dropping as a verified ineffective cell.
- Record this status with the exact cell identifier.
- Record the source engine and template identity.
- Record the actual window and accepted-chain context.
- Record the original calculation date when established.
- Keep status changes append-only or versioned.
- Expose this status in campaign coverage reports.
### Duplicate
- An observation is a replica or repeated scientific identity already counted.
- Preserve provenance but do not inflate independent support.
- Record this status with the exact cell identifier.
- Record the source engine and template identity.
- Record the actual window and accepted-chain context.
- Record the original calculation date when established.
- Keep status changes append-only or versioned.
- Expose this status in campaign coverage reports.
### Unattempted
- The catalog target has no actual attempt yet.
- Keep it in the pending work queue.
- Record this status with the exact cell identifier.
- Record the source engine and template identity.
- Record the actual window and accepted-chain context.
- Record the original calculation date when established.
- Keep status changes append-only or versioned.
- Expose this status in campaign coverage reports.
### Deferred
- A budget or scheduling decision postponed work.
- This is not measured completion and not demotion evidence.
- Record this status with the exact cell identifier.
- Record the source engine and template identity.
- Record the actual window and accepted-chain context.
- Record the original calculation date when established.
- Keep status changes append-only or versioned.
- Expose this status in campaign coverage reports.
### Threshold eligible
- The cell has at least 101 qualifying proven results under the stated identity rules.
- The threshold does not itself manufacture a positive effect.
- Record this status with the exact cell identifier.
- Record the source engine and template identity.
- Record the actual window and accepted-chain context.
- Record the original calculation date when established.
- Keep status changes append-only or versioned.
- Expose this status in campaign coverage reports.
### Promoted
- The authorized evidence rule selected the cell for higher authority or test priority.
- Record the supporting evidence and exact rule; do not bypass S2 production controls.
- Record this status with the exact cell identifier.
- Record the source engine and template identity.
- Record the actual window and accepted-chain context.
- Record the original calculation date when established.
- Keep status changes append-only or versioned.
- Expose this status in campaign coverage reports.
### Demoted
- The authorized evidence rule reduced the cell after the required support threshold.
- Do not erase it from future exploration merely because its old epoch was weak.
- Record this status with the exact cell identifier.
- Record the source engine and template identity.
- Record the actual window and accepted-chain context.
- Record the original calculation date when established.
- Keep status changes append-only or versioned.
- Expose this status in campaign coverage reports.
## 10. Concrete task queue, ordered by urgency
### Task 01: Freeze campaign sources
- Action: Pin the four current template copies and actual runtime sources.
- Evidence to inspect: Read runner_stage_01/inputs/template_sources.json.
- Failure to avoid: Reject source drift during catalog generation.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 02: Complete full Cartesian catalog
- Action: Include every actual row and every filter-option column, not yellow paint alone.
- Evidence to inspect: Inspect catalog.jsonl.gz and its finalized manifest.
- Failure to avoid: Do not treat a still-growing gzip file as a completed catalog.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 03: Keep physical cell mapping
- Action: Retain tab, row, header, option, and source-column references.
- Evidence to inspect: Inspect catalog records and category mappings.
- Failure to avoid: A header borrowed from another tab must not be written into a nonexistent workbook cell.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 04: Prepare accepted sides
- Action: Select independently accepted settings for each campaign sym_side.
- Evidence to inspect: Read exact accepted-source records and their hashes.
- Failure to avoid: Never relabel a SHORT recipe as LONG.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 05: Evaluate baseline
- Action: Run the real baseline on the same prepared inputs used by the campaign.
- Evidence to inspect: Capture config, ledger, result, and cutoff identities.
- Failure to avoid: No baseline substitution by convenience defaults.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 06: Start S6 first batch
- Action: Use the ready immutable runtime and shared verified runner.
- Evidence to inspect: Inspect actual child PID, output receipt, and first measured records.
- Failure to avoid: A staging receipt alone is not a start.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 07: Start S1 batch
- Action: Use its verified runtime copy and live-service-aware worker allocation.
- Evidence to inspect: Verify hostname and actual result output.
- Failure to avoid: Do not overwrite the live sandbox runtime.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 08: Start S2 batch
- Action: Use the verified s2 alias and source copy.
- Evidence to inspect: Verify hostname before any command.
- Failure to avoid: Do not use the previously mistaken gateway address.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 09: Start S5 batch
- Action: Use the verified runtime and available backtest capacity.
- Evidence to inspect: Measure worker RSS and actual CPU use.
- Failure to avoid: Do not confuse currently running preopen workers with campaign workers.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 10: Ramp concurrency
- Action: Increase useful campaign workers as measurements permit.
- Evidence to inspect: Use short CPU samples, process trees, and memory availability.
- Failure to avoid: High load averages alone are not proof of full CPU utilization.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 11: Count first breadth
- Action: Reach a first actual attempt across the full cell catalog.
- Evidence to inspect: Aggregate cell statuses by category and tab.
- Failure to avoid: Do not spend all new work on the first few rows reaching 101.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 12: Recover prior results
- Action: Parse existing indexed progress and delta sources.
- Evidence to inspect: Produce cell-context identities and source receipts.
- Failure to avoid: Raw archive counts cannot be credited without validation.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 13: Deduplicate prior results
- Action: Recognize identical records copied across hosts and backups.
- Evidence to inspect: Persist unique identities and replica references.
- Failure to avoid: Do not sum repeated support from mirrored files.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 14: Join missing lineage
- Action: Resolve source era, window, and baseline from related archives.
- Evidence to inspect: Keep every unresolved join explicit.
- Failure to avoid: No computation-date inference from file modification time.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 15: Verify effective application
- Action: Check that requested row and filter values actually reach the engine.
- Evidence to inspect: Compare actual effective configurations.
- Failure to avoid: Silent drops are not proven zero effects.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 16: Preserve native outcomes
- Action: Retain ledger/result proof or an exact reconstructible representation.
- Evidence to inspect: Validate hashes on readback.
- Failure to avoid: No aggregate-only gain without trade-return provenance.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 17: Compute conditional effects
- Action: Compare combined row/filter result with its actual naked row.
- Evidence to inspect: Keep baseline and joint differences separately.
- Failure to avoid: A helpful conditional effect can coexist with an overall losing combination.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 18: Enforce threshold
- Action: Use at least 101 proven results before authoritative cell changes.
- Evidence to inspect: Store threshold counts and the exact evidence list.
- Failure to avoid: Predictions and repeated identical runs cannot fill the count.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 19: Update v20 evidence
- Action: Feed qualified measurements to the actual learning consumer.
- Evidence to inspect: Link each emitted training shard to consumed sources.
- Failure to avoid: A file list is not absorption.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 20: Protect newly wired cells
- Action: Separate old code-era observations from the current wiring.
- Evidence to inspect: Use actual engine and binding identities.
- Failure to avoid: Do not transfer old UNWIRED negatives into new authority.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 21: Retain historical knowledge
- Action: Preserve prior graphs and model outputs with their source-era labels.
- Evidence to inspect: Check actual consumer use and score influence.
- Failure to avoid: Do not claim exact warm-start from a fresh trainer.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 22: Reconcile phase order
- Action: Bind KG output and first-pass completion before pinpoint.
- Evidence to inspect: Inspect actual callsites, not CLI flag names alone.
- Failure to avoid: Pre-pass research must not silently run ahead of the required first pass.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 23: Repair KG harness contract
- Action: Pass the real canonical trade floor through the actual interface.
- Evidence to inspect: Preserve the failed probe and inspect its raw result.
- Failure to avoid: Do not loosen validation because the probe assumed a missing field.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 24: Apply live authority repair
- Action: Prepare the smallest forward patch on the latest live source.
- Evidence to inspect: Use explicit per-side lookup tests and full unchanged-AST checks.
- Failure to avoid: Do not copy an old reviewed engine over newer unrelated work.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 25: Compare neural networks
- Action: Use the last promoted NN and candidate under the same controlled runtime.
- Evidence to inspect: Run the S2 version gate with frozen inputs and provenance.
- Failure to avoid: Code repairs cannot be credited as NN improvement.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 26: Prove compute reduction
- Action: Measure identical winning NN/input results with the speed candidate.
- Evidence to inspect: Collect CPU, wall time, completed cells, and omitted-cell verification.
- Failure to avoid: Unit test duration is not the 90% proof.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 27: Enable 60D
- Action: Finish and test the actual 60D path under the verified policy.
- Evidence to inspect: Inspect real completed 60D outputs.
- Failure to avoid: A patch or staged command is not a 60D run.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 28: Prove daily catch_up
- Action: Run all required sym_sides on S1/S2 with actual daily completion.
- Evidence to inspect: Inspect freshness, completion, and live-service health.
- Failure to avoid: Do not infer capacity from CPU count alone.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 29: Retire temporary workers
- Action: Only act after evidence preservation and S1/S2 readiness are proven.
- Evidence to inspect: Perform an explicit requirement-by-requirement audit.
- Failure to avoid: No S5/S6 deletion based solely on this handoff.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
### Task 30: Report campaign deadline
- Action: Publish real coverage at the one-hour and two-hour checkpoints.
- Evidence to inspect: Include measured, unresolved, and threshold-qualified counts.
- Failure to avoid: Do not reset the clock or claim completion from scheduled work.
- Owner: root assigns one explicit agent and preserves file ownership.
- Scope: this task serves the v20 redesign and the urgent cell campaign.
- Inputs: freeze the precise source identities used by this task.
- Before execution: check whether an equivalent job is already running.
- Execution: use actual existing code paths whenever they satisfy the requirement.
- Progress: report a measured milestone rather than a repeated intention.
- Validation: inspect the authoritative output, not just a wrapper return code.
- Failure handling: preserve the failed attempt and its original dates.
- Continuation: leave a concrete next action and current process identity.
- Completion: mark this task complete only when its stated evidence exists.
- Integration: connect the output to the downstream consumer that needs it.
- Reporting: state the tested scope and any remaining uncertainty.
## 11. Category-by-tab execution matrix: every work packet must be accounted for
This matrix is an execution checklist for all 52 category/tab combinations.
It is not a claim that those combinations have been evaluated.
The campaign catalog is the authority for actual row and column identities.
The task-specific notes explain what must be observed in each family.
Each packet retains its own coverage state even when exact evaluations can be shared by a proven identical effective request.
### Packet CRYPTO_LONG / STDEV_SLOPE_SIZING
- Category: CRYPTO_LONG; venue family: crypto; side: LONG.
- Template: TEMPLATE_CRYPTO_LONG.xlsx.
- Campaign source SHA-256: d525b63bbf769354ec93e4a0e8079bf72097f252b58f16d0459d3275e44a4a5e.
- Target family: sizing multipliers and their interaction with filter-selected entry opportunities.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Preserve the original position-sizing baseline and distinguish quantity changes from exit changes.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_LONG / ENTRY_REVERSAL_BOUNCE
- Category: CRYPTO_LONG; venue family: crypto; side: LONG.
- Template: TEMPLATE_CRYPTO_LONG.xlsx.
- Campaign source SHA-256: d525b63bbf769354ec93e4a0e8079bf72097f252b58f16d0459d3275e44a4a5e.
- Target family: reversal and bounce entry eligibility under each filter option.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Separate a missing entry opportunity from a filter that actually rejects an otherwise eligible entry.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_LONG / ENTRY_BREAKOUT_CHANNEL
- Category: CRYPTO_LONG; venue family: crypto; side: LONG.
- Template: TEMPLATE_CRYPTO_LONG.xlsx.
- Campaign source SHA-256: d525b63bbf769354ec93e4a0e8079bf72097f252b58f16d0459d3275e44a4a5e.
- Target family: breakout and channel entry behavior under each filter option.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Pin the channel arrays and distinguish timeframe availability from a genuine non-binding result.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_LONG / ENTRY_CONFIRMATION_GATES
- Category: CRYPTO_LONG; venue family: crypto; side: LONG.
- Template: TEMPLATE_CRYPTO_LONG.xlsx.
- Campaign source SHA-256: d525b63bbf769354ec93e4a0e8079bf72097f252b58f16d0459d3275e44a4a5e.
- Target family: entry confirmation and gate combinations.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Verify a selected False is not silently overwritten by a template or global gate.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_LONG / EXIT_STRUCTURAL
- Category: CRYPTO_LONG; venue family: crypto; side: LONG.
- Template: TEMPLATE_CRYPTO_LONG.xlsx.
- Campaign source SHA-256: d525b63bbf769354ec93e4a0e8079bf72097f252b58f16d0459d3275e44a4a5e.
- Target family: structural exits, mandatory stops, and protection interactions.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Retain mandatory fixed-stop behavior while testing the selected profit-protection settings.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_LONG / EXIT_VELOCITY
- Category: CRYPTO_LONG; venue family: crypto; side: LONG.
- Template: TEMPLATE_CRYPTO_LONG.xlsx.
- Campaign source SHA-256: d525b63bbf769354ec93e4a0e8079bf72097f252b58f16d0459d3275e44a4a5e.
- Target family: velocity, momentum, and exit-score relationships.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Capture the native score effect separately from whether downstream policy books an exit.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_LONG / REENTRY_WINDOWED
- Category: CRYPTO_LONG; venue family: crypto; side: LONG.
- Template: TEMPLATE_CRYPTO_LONG.xlsx.
- Campaign source SHA-256: d525b63bbf769354ec93e4a0e8079bf72097f252b58f16d0459d3275e44a4a5e.
- Target family: time-windowed recovery and reentry decisions.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Check the actual recovered entry and its downstream gates rather than counting a trigger alone.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_LONG / REENTRY_ADAPTIVE
- Category: CRYPTO_LONG; venue family: crypto; side: LONG.
- Template: TEMPLATE_CRYPTO_LONG.xlsx.
- Campaign source SHA-256: d525b63bbf769354ec93e4a0e8079bf72097f252b58f16d0459d3275e44a4a5e.
- Target family: adaptive recovery conditions and reentry filters.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Preserve side identity and record whether an unselected hardcoded veto cleared the signal.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_LONG / AUGMENT_TREND
- Category: CRYPTO_LONG; venue family: crypto; side: LONG.
- Template: TEMPLATE_CRYPTO_LONG.xlsx.
- Campaign source SHA-256: d525b63bbf769354ec93e4a0e8079bf72097f252b58f16d0459d3275e44a4a5e.
- Target family: trend-based position augmentation under filters.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Distinguish additional exposure from initial opens and retain actual sizing effects.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_LONG / AUGMENT_RISK_SIZING
- Category: CRYPTO_LONG; venue family: crypto; side: LONG.
- Template: TEMPLATE_CRYPTO_LONG.xlsx.
- Campaign source SHA-256: d525b63bbf769354ec93e4a0e8079bf72097f252b58f16d0459d3275e44a4a5e.
- Target family: risk and sizing adjustments for existing or new exposure.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Respect the template writer default-group hoist when verifying physical worst-first order.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_LONG / REDUCE_PROFIT_LOCK
- Category: CRYPTO_LONG; venue family: crypto; side: LONG.
- Template: TEMPLATE_CRYPTO_LONG.xlsx.
- Campaign source SHA-256: d525b63bbf769354ec93e4a0e8079bf72097f252b58f16d0459d3275e44a4a5e.
- Target family: profit locking, peak giveback, and breakeven-related behavior.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Evaluate conditional filter effects without promoting a joint combination that loses against baseline.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_LONG / REDUCE_SIGNAL_RATER
- Category: CRYPTO_LONG; venue family: crypto; side: LONG.
- Template: TEMPLATE_CRYPTO_LONG.xlsx.
- Campaign source SHA-256: d525b63bbf769354ec93e4a0e8079bf72097f252b58f16d0459d3275e44a4a5e.
- Target family: partial-reduction signals and scoring filters.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Avoid double counting lifecycle PnL when several partial ledger events refer to one position.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_LONG / GLOBAL_RISK_GATES
- Category: CRYPTO_LONG; venue family: crypto; side: LONG.
- Template: TEMPLATE_CRYPTO_LONG.xlsx.
- Campaign source SHA-256: d525b63bbf769354ec93e4a0e8079bf72097f252b58f16d0459d3275e44a4a5e.
- Target family: global strategy gate options as applied to each row.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Do not let a config fallback replace the selected row/filter value under the guise of a gate.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_SHORT / STDEV_SLOPE_SIZING
- Category: CRYPTO_SHORT; venue family: crypto; side: SHORT.
- Template: TEMPLATE_CRYPTO_SHORT.xlsx.
- Campaign source SHA-256: caa1371d97bf13d9e8918dd8cbb630baf9bd3153acc4914444d6f58e3fce647f.
- Target family: sizing multipliers and their interaction with filter-selected entry opportunities.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Preserve the original position-sizing baseline and distinguish quantity changes from exit changes.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_SHORT / ENTRY_REVERSAL_BOUNCE
- Category: CRYPTO_SHORT; venue family: crypto; side: SHORT.
- Template: TEMPLATE_CRYPTO_SHORT.xlsx.
- Campaign source SHA-256: caa1371d97bf13d9e8918dd8cbb630baf9bd3153acc4914444d6f58e3fce647f.
- Target family: reversal and bounce entry eligibility under each filter option.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Separate a missing entry opportunity from a filter that actually rejects an otherwise eligible entry.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_SHORT / ENTRY_BREAKOUT_CHANNEL
- Category: CRYPTO_SHORT; venue family: crypto; side: SHORT.
- Template: TEMPLATE_CRYPTO_SHORT.xlsx.
- Campaign source SHA-256: caa1371d97bf13d9e8918dd8cbb630baf9bd3153acc4914444d6f58e3fce647f.
- Target family: breakout and channel entry behavior under each filter option.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Pin the channel arrays and distinguish timeframe availability from a genuine non-binding result.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_SHORT / ENTRY_CONFIRMATION_GATES
- Category: CRYPTO_SHORT; venue family: crypto; side: SHORT.
- Template: TEMPLATE_CRYPTO_SHORT.xlsx.
- Campaign source SHA-256: caa1371d97bf13d9e8918dd8cbb630baf9bd3153acc4914444d6f58e3fce647f.
- Target family: entry confirmation and gate combinations.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Verify a selected False is not silently overwritten by a template or global gate.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_SHORT / EXIT_STRUCTURAL
- Category: CRYPTO_SHORT; venue family: crypto; side: SHORT.
- Template: TEMPLATE_CRYPTO_SHORT.xlsx.
- Campaign source SHA-256: caa1371d97bf13d9e8918dd8cbb630baf9bd3153acc4914444d6f58e3fce647f.
- Target family: structural exits, mandatory stops, and protection interactions.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Retain mandatory fixed-stop behavior while testing the selected profit-protection settings.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_SHORT / EXIT_VELOCITY
- Category: CRYPTO_SHORT; venue family: crypto; side: SHORT.
- Template: TEMPLATE_CRYPTO_SHORT.xlsx.
- Campaign source SHA-256: caa1371d97bf13d9e8918dd8cbb630baf9bd3153acc4914444d6f58e3fce647f.
- Target family: velocity, momentum, and exit-score relationships.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Capture the native score effect separately from whether downstream policy books an exit.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_SHORT / REENTRY_WINDOWED
- Category: CRYPTO_SHORT; venue family: crypto; side: SHORT.
- Template: TEMPLATE_CRYPTO_SHORT.xlsx.
- Campaign source SHA-256: caa1371d97bf13d9e8918dd8cbb630baf9bd3153acc4914444d6f58e3fce647f.
- Target family: time-windowed recovery and reentry decisions.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Check the actual recovered entry and its downstream gates rather than counting a trigger alone.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_SHORT / REENTRY_ADAPTIVE
- Category: CRYPTO_SHORT; venue family: crypto; side: SHORT.
- Template: TEMPLATE_CRYPTO_SHORT.xlsx.
- Campaign source SHA-256: caa1371d97bf13d9e8918dd8cbb630baf9bd3153acc4914444d6f58e3fce647f.
- Target family: adaptive recovery conditions and reentry filters.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Preserve side identity and record whether an unselected hardcoded veto cleared the signal.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_SHORT / AUGMENT_TREND
- Category: CRYPTO_SHORT; venue family: crypto; side: SHORT.
- Template: TEMPLATE_CRYPTO_SHORT.xlsx.
- Campaign source SHA-256: caa1371d97bf13d9e8918dd8cbb630baf9bd3153acc4914444d6f58e3fce647f.
- Target family: trend-based position augmentation under filters.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Distinguish additional exposure from initial opens and retain actual sizing effects.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_SHORT / AUGMENT_RISK_SIZING
- Category: CRYPTO_SHORT; venue family: crypto; side: SHORT.
- Template: TEMPLATE_CRYPTO_SHORT.xlsx.
- Campaign source SHA-256: caa1371d97bf13d9e8918dd8cbb630baf9bd3153acc4914444d6f58e3fce647f.
- Target family: risk and sizing adjustments for existing or new exposure.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Respect the template writer default-group hoist when verifying physical worst-first order.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_SHORT / REDUCE_PROFIT_LOCK
- Category: CRYPTO_SHORT; venue family: crypto; side: SHORT.
- Template: TEMPLATE_CRYPTO_SHORT.xlsx.
- Campaign source SHA-256: caa1371d97bf13d9e8918dd8cbb630baf9bd3153acc4914444d6f58e3fce647f.
- Target family: profit locking, peak giveback, and breakeven-related behavior.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Evaluate conditional filter effects without promoting a joint combination that loses against baseline.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_SHORT / REDUCE_SIGNAL_RATER
- Category: CRYPTO_SHORT; venue family: crypto; side: SHORT.
- Template: TEMPLATE_CRYPTO_SHORT.xlsx.
- Campaign source SHA-256: caa1371d97bf13d9e8918dd8cbb630baf9bd3153acc4914444d6f58e3fce647f.
- Target family: partial-reduction signals and scoring filters.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Avoid double counting lifecycle PnL when several partial ledger events refer to one position.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet CRYPTO_SHORT / GLOBAL_RISK_GATES
- Category: CRYPTO_SHORT; venue family: crypto; side: SHORT.
- Template: TEMPLATE_CRYPTO_SHORT.xlsx.
- Campaign source SHA-256: caa1371d97bf13d9e8918dd8cbb630baf9bd3153acc4914444d6f58e3fce647f.
- Target family: global strategy gate options as applied to each row.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Do not let a config fallback replace the selected row/filter value under the guise of a gate.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_LONG / STDEV_SLOPE_SIZING
- Category: STOCKS_LONG; venue family: stocks; side: LONG.
- Template: TEMPLATE_STOCKS_LONG.xlsx.
- Campaign source SHA-256: bf208dfa6de67d471a85e02d460b207dad01a9cf0f6f5a6a3c1b29f22fe82396.
- Target family: sizing multipliers and their interaction with filter-selected entry opportunities.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Preserve the original position-sizing baseline and distinguish quantity changes from exit changes.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_LONG / ENTRY_REVERSAL_BOUNCE
- Category: STOCKS_LONG; venue family: stocks; side: LONG.
- Template: TEMPLATE_STOCKS_LONG.xlsx.
- Campaign source SHA-256: bf208dfa6de67d471a85e02d460b207dad01a9cf0f6f5a6a3c1b29f22fe82396.
- Target family: reversal and bounce entry eligibility under each filter option.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Separate a missing entry opportunity from a filter that actually rejects an otherwise eligible entry.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_LONG / ENTRY_BREAKOUT_CHANNEL
- Category: STOCKS_LONG; venue family: stocks; side: LONG.
- Template: TEMPLATE_STOCKS_LONG.xlsx.
- Campaign source SHA-256: bf208dfa6de67d471a85e02d460b207dad01a9cf0f6f5a6a3c1b29f22fe82396.
- Target family: breakout and channel entry behavior under each filter option.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Pin the channel arrays and distinguish timeframe availability from a genuine non-binding result.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_LONG / ENTRY_CONFIRMATION_GATES
- Category: STOCKS_LONG; venue family: stocks; side: LONG.
- Template: TEMPLATE_STOCKS_LONG.xlsx.
- Campaign source SHA-256: bf208dfa6de67d471a85e02d460b207dad01a9cf0f6f5a6a3c1b29f22fe82396.
- Target family: entry confirmation and gate combinations.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Verify a selected False is not silently overwritten by a template or global gate.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_LONG / EXIT_STRUCTURAL
- Category: STOCKS_LONG; venue family: stocks; side: LONG.
- Template: TEMPLATE_STOCKS_LONG.xlsx.
- Campaign source SHA-256: bf208dfa6de67d471a85e02d460b207dad01a9cf0f6f5a6a3c1b29f22fe82396.
- Target family: structural exits, mandatory stops, and protection interactions.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Retain mandatory fixed-stop behavior while testing the selected profit-protection settings.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_LONG / EXIT_VELOCITY
- Category: STOCKS_LONG; venue family: stocks; side: LONG.
- Template: TEMPLATE_STOCKS_LONG.xlsx.
- Campaign source SHA-256: bf208dfa6de67d471a85e02d460b207dad01a9cf0f6f5a6a3c1b29f22fe82396.
- Target family: velocity, momentum, and exit-score relationships.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Capture the native score effect separately from whether downstream policy books an exit.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_LONG / REENTRY_WINDOWED
- Category: STOCKS_LONG; venue family: stocks; side: LONG.
- Template: TEMPLATE_STOCKS_LONG.xlsx.
- Campaign source SHA-256: bf208dfa6de67d471a85e02d460b207dad01a9cf0f6f5a6a3c1b29f22fe82396.
- Target family: time-windowed recovery and reentry decisions.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Check the actual recovered entry and its downstream gates rather than counting a trigger alone.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_LONG / REENTRY_ADAPTIVE
- Category: STOCKS_LONG; venue family: stocks; side: LONG.
- Template: TEMPLATE_STOCKS_LONG.xlsx.
- Campaign source SHA-256: bf208dfa6de67d471a85e02d460b207dad01a9cf0f6f5a6a3c1b29f22fe82396.
- Target family: adaptive recovery conditions and reentry filters.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Preserve side identity and record whether an unselected hardcoded veto cleared the signal.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_LONG / AUGMENT_TREND
- Category: STOCKS_LONG; venue family: stocks; side: LONG.
- Template: TEMPLATE_STOCKS_LONG.xlsx.
- Campaign source SHA-256: bf208dfa6de67d471a85e02d460b207dad01a9cf0f6f5a6a3c1b29f22fe82396.
- Target family: trend-based position augmentation under filters.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Distinguish additional exposure from initial opens and retain actual sizing effects.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_LONG / AUGMENT_RISK_SIZING
- Category: STOCKS_LONG; venue family: stocks; side: LONG.
- Template: TEMPLATE_STOCKS_LONG.xlsx.
- Campaign source SHA-256: bf208dfa6de67d471a85e02d460b207dad01a9cf0f6f5a6a3c1b29f22fe82396.
- Target family: risk and sizing adjustments for existing or new exposure.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Respect the template writer default-group hoist when verifying physical worst-first order.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_LONG / REDUCE_PROFIT_LOCK
- Category: STOCKS_LONG; venue family: stocks; side: LONG.
- Template: TEMPLATE_STOCKS_LONG.xlsx.
- Campaign source SHA-256: bf208dfa6de67d471a85e02d460b207dad01a9cf0f6f5a6a3c1b29f22fe82396.
- Target family: profit locking, peak giveback, and breakeven-related behavior.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Evaluate conditional filter effects without promoting a joint combination that loses against baseline.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_LONG / REDUCE_SIGNAL_RATER
- Category: STOCKS_LONG; venue family: stocks; side: LONG.
- Template: TEMPLATE_STOCKS_LONG.xlsx.
- Campaign source SHA-256: bf208dfa6de67d471a85e02d460b207dad01a9cf0f6f5a6a3c1b29f22fe82396.
- Target family: partial-reduction signals and scoring filters.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Avoid double counting lifecycle PnL when several partial ledger events refer to one position.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_LONG / GLOBAL_RISK_GATES
- Category: STOCKS_LONG; venue family: stocks; side: LONG.
- Template: TEMPLATE_STOCKS_LONG.xlsx.
- Campaign source SHA-256: bf208dfa6de67d471a85e02d460b207dad01a9cf0f6f5a6a3c1b29f22fe82396.
- Target family: global strategy gate options as applied to each row.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted LONG settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Do not let a config fallback replace the selected row/filter value under the guise of a gate.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_SHORT / STDEV_SLOPE_SIZING
- Category: STOCKS_SHORT; venue family: stocks; side: SHORT.
- Template: TEMPLATE_STOCKS_SHORT.xlsx.
- Campaign source SHA-256: 57ab03b3161ac79673fb410e0a690e9056c6dd76d2e0535276ebfa6201c8a0b1.
- Target family: sizing multipliers and their interaction with filter-selected entry opportunities.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Preserve the original position-sizing baseline and distinguish quantity changes from exit changes.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_SHORT / ENTRY_REVERSAL_BOUNCE
- Category: STOCKS_SHORT; venue family: stocks; side: SHORT.
- Template: TEMPLATE_STOCKS_SHORT.xlsx.
- Campaign source SHA-256: 57ab03b3161ac79673fb410e0a690e9056c6dd76d2e0535276ebfa6201c8a0b1.
- Target family: reversal and bounce entry eligibility under each filter option.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Separate a missing entry opportunity from a filter that actually rejects an otherwise eligible entry.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_SHORT / ENTRY_BREAKOUT_CHANNEL
- Category: STOCKS_SHORT; venue family: stocks; side: SHORT.
- Template: TEMPLATE_STOCKS_SHORT.xlsx.
- Campaign source SHA-256: 57ab03b3161ac79673fb410e0a690e9056c6dd76d2e0535276ebfa6201c8a0b1.
- Target family: breakout and channel entry behavior under each filter option.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Pin the channel arrays and distinguish timeframe availability from a genuine non-binding result.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_SHORT / ENTRY_CONFIRMATION_GATES
- Category: STOCKS_SHORT; venue family: stocks; side: SHORT.
- Template: TEMPLATE_STOCKS_SHORT.xlsx.
- Campaign source SHA-256: 57ab03b3161ac79673fb410e0a690e9056c6dd76d2e0535276ebfa6201c8a0b1.
- Target family: entry confirmation and gate combinations.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Verify a selected False is not silently overwritten by a template or global gate.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_SHORT / EXIT_STRUCTURAL
- Category: STOCKS_SHORT; venue family: stocks; side: SHORT.
- Template: TEMPLATE_STOCKS_SHORT.xlsx.
- Campaign source SHA-256: 57ab03b3161ac79673fb410e0a690e9056c6dd76d2e0535276ebfa6201c8a0b1.
- Target family: structural exits, mandatory stops, and protection interactions.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Retain mandatory fixed-stop behavior while testing the selected profit-protection settings.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_SHORT / EXIT_VELOCITY
- Category: STOCKS_SHORT; venue family: stocks; side: SHORT.
- Template: TEMPLATE_STOCKS_SHORT.xlsx.
- Campaign source SHA-256: 57ab03b3161ac79673fb410e0a690e9056c6dd76d2e0535276ebfa6201c8a0b1.
- Target family: velocity, momentum, and exit-score relationships.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Capture the native score effect separately from whether downstream policy books an exit.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_SHORT / REENTRY_WINDOWED
- Category: STOCKS_SHORT; venue family: stocks; side: SHORT.
- Template: TEMPLATE_STOCKS_SHORT.xlsx.
- Campaign source SHA-256: 57ab03b3161ac79673fb410e0a690e9056c6dd76d2e0535276ebfa6201c8a0b1.
- Target family: time-windowed recovery and reentry decisions.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Check the actual recovered entry and its downstream gates rather than counting a trigger alone.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_SHORT / REENTRY_ADAPTIVE
- Category: STOCKS_SHORT; venue family: stocks; side: SHORT.
- Template: TEMPLATE_STOCKS_SHORT.xlsx.
- Campaign source SHA-256: 57ab03b3161ac79673fb410e0a690e9056c6dd76d2e0535276ebfa6201c8a0b1.
- Target family: adaptive recovery conditions and reentry filters.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Preserve side identity and record whether an unselected hardcoded veto cleared the signal.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_SHORT / AUGMENT_TREND
- Category: STOCKS_SHORT; venue family: stocks; side: SHORT.
- Template: TEMPLATE_STOCKS_SHORT.xlsx.
- Campaign source SHA-256: 57ab03b3161ac79673fb410e0a690e9056c6dd76d2e0535276ebfa6201c8a0b1.
- Target family: trend-based position augmentation under filters.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Distinguish additional exposure from initial opens and retain actual sizing effects.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_SHORT / AUGMENT_RISK_SIZING
- Category: STOCKS_SHORT; venue family: stocks; side: SHORT.
- Template: TEMPLATE_STOCKS_SHORT.xlsx.
- Campaign source SHA-256: 57ab03b3161ac79673fb410e0a690e9056c6dd76d2e0535276ebfa6201c8a0b1.
- Target family: risk and sizing adjustments for existing or new exposure.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Respect the template writer default-group hoist when verifying physical worst-first order.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_SHORT / REDUCE_PROFIT_LOCK
- Category: STOCKS_SHORT; venue family: stocks; side: SHORT.
- Template: TEMPLATE_STOCKS_SHORT.xlsx.
- Campaign source SHA-256: 57ab03b3161ac79673fb410e0a690e9056c6dd76d2e0535276ebfa6201c8a0b1.
- Target family: profit locking, peak giveback, and breakeven-related behavior.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Evaluate conditional filter effects without promoting a joint combination that loses against baseline.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_SHORT / REDUCE_SIGNAL_RATER
- Category: STOCKS_SHORT; venue family: stocks; side: SHORT.
- Template: TEMPLATE_STOCKS_SHORT.xlsx.
- Campaign source SHA-256: 57ab03b3161ac79673fb410e0a690e9056c6dd76d2e0535276ebfa6201c8a0b1.
- Target family: partial-reduction signals and scoring filters.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Avoid double counting lifecycle PnL when several partial ledger events refer to one position.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
### Packet STOCKS_SHORT / GLOBAL_RISK_GATES
- Category: STOCKS_SHORT; venue family: stocks; side: SHORT.
- Template: TEMPLATE_STOCKS_SHORT.xlsx.
- Campaign source SHA-256: 57ab03b3161ac79673fb410e0a690e9056c6dd76d2e0535276ebfa6201c8a0b1.
- Target family: global strategy gate options as applied to each row.
- Include every catalogued row in this tab; do not filter rows by historical pos_sym.
- Include every campaign filter-option header for the category; do not use current yellow paint as an exclusion.
- Keep the exact physical tab/row identifier and the logical switch/candidate identifier together.
- Keep the actual source column references for each filter option.
- For headers absent physically from this tab, preserve their research relationship identity separately from workbook coordinates.
- Load independently accepted SHORT settings for the selected symbol; never borrow the opposite-side recipe.
- Compute the accepted baseline on the same frozen NPZ used by all requests in this packet.
- Compute the naked row result before interpreting its conditional filter effects.
- Record combined-minus-baseline and combined-minus-naked effects separately.
- Verify the requested switch candidate survives actual effective-config construction.
- Verify the requested filter option survives actual effective-config construction.
- Mark unsupported fields, unavailable data, and contradictory self-field requests explicitly.
- Keep completed but no-exposure observations distinguishable from informative observations.
- Keep genuine measured zeros; never manufacture them for skipped or failed work.
- Deduplicate only when exact request/input/runtime equivalence is proven, retaining all cell-to-evaluation links.
- Start with breadth-first coverage, then schedule the lowest-supported cells toward 101 qualifying results.
- Count distinct qualifying results for this cell identity rather than duplicated file copies.
- Record the source computation time; do not substitute a publication or file modification time.
- Preserve each actual ledger/result proof in an exact retrievable or reconstructible form.
- Write explicit partial coverage if the deadline arrives before this packet finishes.
- Only promote or demote with the required support threshold and the actual evidence rule.
- Feed these measurements into the v20 relationship consumer with code-era and window context.
- Special family check: Do not let a config fallback replace the selected row/filter value under the guise of a gate.
- Completion evidence: catalog total reconciles with measured, invalid, not-applied, and pending/deferred records.
- Do not mark this packet complete from a process launch, a template color, or a generic ROW_DONE counter.
## 12. Source-family ingestion work packets
These work packets connect the complete archive objective to actual learning consumption.
During the urgent two-hour campaign, indexed sources with usable cell evidence take priority.
Do not discard the remaining source families when the deadline passes.
### Ingestion packet 01: Lifecycle progress JSON
- Location pattern or source family: data/reports/lifecycle_pilot/*_v14_progress.json.
- Information to apply: done rows, exact yellows, accepted sets, computation markers.
- Primary integrity check: Parse each cell status and recover its baseline/window/source identity.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 02: Per-evaluation delta logs
- Location pattern or source family: v15_run*/progress/v15_delta_log/*.jsonl.
- Information to apply: individual evaluations and original calculation timestamps.
- Primary integrity check: Join row/filter/context records rather than substituting top-level board metadata.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 03: Completed workbooks
- Location pattern or source family: SPREADSHEETS/V15_V16_CELL_BY_CELL/.
- Information to apply: human-facing row and column mapping plus stored overrides.
- Primary integrity check: Cross-check workbook values against calculation records before trusting labels.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 04: Template revisions
- Location pattern or source family: SPREADSHEETS/TEMPLATE_FINAL_NORM/.
- Information to apply: row/header definitions, types, defaults, ordering, and color history.
- Primary integrity check: Keep each content hash as a separate template era.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 05: Accepted settings histories
- Location pattern or source family: per_sym history exports and source database snapshots.
- Information to apply: which settings were accepted for an exact symbol side.
- Primary integrity check: Do not mistake a later stored timestamp for the earlier computation date.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 06: Per-symbol full sets
- Location pattern or source family: data/per_sym_full_sets.json and verified historical copies.
- Information to apply: source-bound full selected settings.
- Primary integrity check: Capture each category and side independently.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 07: Evaluation cache
- Location pattern or source family: verified eval_cache database snapshots.
- Information to apply: memoized actual evaluations keyed by request context.
- Primary integrity check: Require coherent snapshot and complete cache-key semantics before reusing records.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 08: Causal learner database
- Location pattern or source family: data/causal_learner.db.
- Information to apply: historical relationships and contextual records.
- Primary integrity check: Preserve the original schema and distinguish associations from verified interventions.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 09: Combo library
- Location pattern or source family: v17_combo_library.sqlite.
- Information to apply: previous tested combinations.
- Primary integrity check: Recover exact baseline and component evidence before treating a combination as a factorial proof.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 10: Archived NPZ files
- Location pattern or source family: 5T verified market-data history.
- Information to apply: frozen numerical inputs and time windows.
- Primary integrity check: Use actual bytes and array identities rather than filename dates alone.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 11: Kline archives
- Location pattern or source family: 5T source-specific market history.
- Information to apply: bar data underlying indicator recomputation.
- Primary integrity check: Retain source timestamps and distinguish closed bars from forming bars.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 12: Indicator manifests
- Location pattern or source family: historical precompute and snapshot manifests.
- Information to apply: array availability and feature versions.
- Primary integrity check: Use these to recover whether a requested timeframe existed during an old evaluation.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 13: Engine source snapshots
- Location pattern or source family: backups and source-pinned runtime manifests.
- Information to apply: the actual implementation that produced a result.
- Primary integrity check: Do not merge evidence from different wiring eras without an explicit rule.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 14: Pilot source snapshots
- Location pattern or source family: version-gate payloads and backups.
- Information to apply: selection and phase behavior used for each board.
- Primary integrity check: Separate policy changes from engine changes.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 15: Evaluator source snapshots
- Location pattern or source family: tools/opt/evaluate_v12.py revisions.
- Information to apply: override application and dependency transformations.
- Primary integrity check: Check for template relayering that overwrote selected values.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 16: Training spills
- Location pattern or source family: spill_*.tsv and spill_*.tsv.gz.
- Information to apply: events already extracted for graph or neural training.
- Primary integrity check: Preserve original event IDs and detect lossy extraction before using threshold counts.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 17: Sorted event artifacts
- Location pattern or source family: events_sorted.tsv or verified gzip copies.
- Information to apply: exact event ordering used or proposed for training.
- Primary integrity check: Do not claim linkage to a model when the training manifest disagrees.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 18: Graph exports
- Location pattern or source family: brain_union and prior graph JSON exports.
- Information to apply: learned support and relationship summaries.
- Primary integrity check: Keep source-specific counts without summing overlapping populations.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 19: Graph state checkpoints
- Location pattern or source family: v18_brain_state.pkl in verified own artifacts.
- Information to apply: richer graph state and saved tables.
- Primary integrity check: Treat the actual source host correctly; a task destination name does not establish source provenance.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 20: LinkNet model files
- Location pattern or source family: model_v1/model_v2 NPZ artifacts.
- Information to apply: saved weights and feature vocabularies.
- Primary integrity check: Keep vocab and xvocab tied to the exact saved model.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 21: Neural proposal files
- Location pattern or source family: ranked untried pair exports.
- Information to apply: predictions for future testing.
- Primary integrity check: Never count predicted probability as a proven backtest result.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 22: Prior promotion manifests
- Location pattern or source family: S2 gate champion/candidate metadata.
- Information to apply: the actual last promoted network identity.
- Primary integrity check: An October 23 PASS can refer to the same October 22 payload; compare hashes.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 23: Training reports
- Location pattern or source family: trainer metadata and evaluation reports.
- Information to apply: cutoffs, actual consumed counts, and model roles.
- Primary integrity check: Do not infer exact optimizer continuation from a saved weights file.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 24: Live trade-return lists
- Location pattern or source family: per_trade_returns and actual broker-linked histories.
- Information to apply: actual returns for validation and metrics.
- Primary integrity check: Preserve fees and partial-fill/lifecycle semantics.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 25: Position-chain handoffs
- Location pattern or source family: position_chain investigation exports.
- Information to apply: potential linkage between settings and live fills.
- Primary integrity check: An unverified attribution must not become a supervised causal label.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 26: Profit-protection variation results
- Location pattern or source family: gain_protection_variation_lab outputs.
- Information to apply: actual evaluated protective variations.
- Primary integrity check: Use full report identities where a narrower lifecycle hash omitted a PnL field.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 27: Reentry traces
- Location pattern or source family: native protection/recovery trace artifacts.
- Information to apply: actual signal clearing and booking paths.
- Primary integrity check: Distinguish a true trigger from an executed reentry.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 28: Per-cell evidence summaries
- Location pattern or source family: v15_cell_evidence derived outputs.
- Information to apply: existing aggregate cell information.
- Primary integrity check: Require underlying unique observations before counting toward 101.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 29: Yellow partial sets
- Location pattern or source family: yellow_partials and yellow_ever_nonzero.
- Information to apply: historical sets of observed or painted cell IDs.
- Primary integrity check: A set does not supply event counts or distinct-trial proof.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 30: Daily averages
- Location pattern or source family: avg_delta and pos_sym snapshots.
- Information to apply: historical scheduling summaries.
- Primary integrity check: Keep their population and epoch definitions, especially for newly wired rows.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 31: Source synchronization receipts
- Location pattern or source family: 5T copy and verification logs.
- Information to apply: which bytes were retained from each host.
- Primary integrity check: Storage verification is not model consumption.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 32: External-drive archives
- Location pattern or source family: 5T WD25/TOSHIBA and other verified drive data.
- Information to apply: older results and preserved models.
- Primary integrity check: Resolve absent top-level roots without assuming missing data or fabricating coverage.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 33: Malformed records
- Location pattern or source family: quarantined parse failures.
- Information to apply: evidence that ingestion encountered unusable data.
- Primary integrity check: Preserve raw bytes and a parse reason so a later repair can recover them.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 34: Conflicting duplicate IDs
- Location pattern or source family: events sharing a logical ID but disagreeing in content.
- Information to apply: data-integrity conflicts requiring resolution.
- Primary integrity check: Never choose the higher gain silently.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 35: New campaign receipts
- Location pattern or source family: full_cell_campaign outputs.
- Information to apply: fresh exact row/filter measurements.
- Primary integrity check: Link every catalog target to its actual evaluation or explicit unresolved status.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
### Ingestion packet 36: Rejected candidate records
- Location pattern or source family: validly computed losing or inadmissible variations.
- Information to apply: negative and constraint-related learning evidence.
- Primary integrity check: Do not discard losses because they are inconvenient to model promotion.
- Record the source host and archived relative path.
- Record the content hash of the exact bytes read.
- Record whether the source was immutable, snapshotted, or changing during observation.
- Do not call a live database file plus unrelated WAL bytes a coherent snapshot.
- Keep a resumable extraction position and a terminal source status.
- Count parsed records, malformed records, duplicates, and unresolved records separately.
- Preserve original calculation timestamps where they are actually present.
- Use related archived records to repair missing lineage rather than guessing it.
- Retain replica references while preventing duplicate support inflation.
- Separate historical/context use from verified gain-label use.
- Separate research final-fit use from pre-holdout-qualified use.
- Write emitted shard hashes and the row/event identities they contain.
- Write actual trainer-consumption receipts for those emitted shards.
- Reconcile consumed counts with the extractor ledger.
- Do not mark this packet applied from an archive listing alone.
- Keep unresolved portions in the queue after the urgent coverage deadline.
- Connect the resulting evidence to v20 rather than leaving it as an unused side report.
## 13. Important runtime and artifact identities
### Canonical local pilot after earlier authorized forward patch
- SHA-256: `e91ed048ecf972d899b243c843372476e35c1cddb70c06d3f23fe453c25de0a8`.
- Scope: Local only; verify current bytes before any merge.
- Revalidate the referenced file before relying on this identifier.
- A newer file with the same name must not inherit this proof.
### Historical actual reference engine
- SHA-256: `c86aded579e52f23ba8865de0d8b46a7f72b4e71ecfeab136ac5510567fb64fa`.
- Scope: Historical frozen runtime; not the latest Mac engine.
- Revalidate the referenced file before relying on this identifier.
- A newer file with the same name must not inherit this proof.
### Historical corrected engine
- SHA-256: `49410495ef3745cd8cd9cc2502f6dfba01ea3fe542b4bb8c07aa2c2c8e557b4f`.
- Scope: Measured isolated authority correction.
- Revalidate the referenced file before relying on this identifier.
- A newer file with the same name must not inherit this proof.
### Corrected evaluator
- SHA-256: `211b54b45835adf09153552304d4bb43d35ef1f145c58ab0f37c80a64e902493`.
- Scope: Preserves the prepared template-layer identity.
- Revalidate the referenced file before relying on this identifier.
- A newer file with the same name must not inherit this proof.
### Original evaluator
- SHA-256: `555ecba9821093f54079d97ff4863f2b904ba7f552207ca702345797a443f0f5`.
- Scope: Canonical remained at this hash at the last root inspection.
- Revalidate the referenced file before relying on this identifier.
- A newer file with the same name must not inherit this proof.
### Reviewed KG helper
- SHA-256: `5e5c62810e499bb16170bac02dd529d3a5c496638172c1a73a99fcd4dcb80c80`.
- Scope: Review code; real floor must be supplied through the correct contract.
- Revalidate the referenced file before relying on this identifier.
- A newer file with the same name must not inherit this proof.
### Two-field corrected engine
- SHA-256: `b287467d1dc0e66e0fda7b81fc4bf2ddde49f186b6b0a83cf5fbeb9cdb6d5a43`.
- Scope: Adds only the two explicit False-default real fields to the measured corrected era.
- Revalidate the referenced file before relying on this identifier.
- A newer file with the same name must not inherit this proof.
### Original crypto actual oracle
- SHA-256: `0a822a68a84275b6b1c71c7e38c005c55c0e65a25b3d70c026450f296bb8591a`.
- Scope: Preserved genuine previous baseline proof.
- Revalidate the referenced file before relying on this identifier.
- A newer file with the same name must not inherit this proof.
### Combined crypto authority oracle
- SHA-256: `328fd8c00b334c4b1d2e08a920c8a792c237f40d0159a1ddc25a8e3f1f964746`.
- Scope: Expected corrected control, not an unchanged historical runtime claim.
- Revalidate the referenced file before relying on this identifier.
- A newer file with the same name must not inherit this proof.
### Combined IBM authority oracle
- SHA-256: `ff5e5899ffba315638fd78c24e6840911c2b3d6c13b84c46b620b4e7c5f517b4`.
- Scope: Independent stock corrected control.
- Revalidate the referenced file before relying on this identifier.
- A newer file with the same name must not inherit this proof.
### Current-forward engine snapshot reviewed earlier
- SHA-256: `71b897854f7f4ec671de6ac0bcf9108751cdf5bf4de90a6eed75809502ac9d96`.
- Scope: The Mac engine changed again afterward; never overwrite newer work with it.
- Revalidate the referenced file before relying on this identifier.
- A newer file with the same name must not inherit this proof.
### Current-forward minimal candidate
- SHA-256: `ed50a9d244f91d237602300f824167ef1cd66f1755a419125fe9776ef077a7f5`.
- Scope: Whole-AST preservation test passed for the snapshot, not for every later source.
- Revalidate the referenced file before relying on this identifier.
- A newer file with the same name must not inherit this proof.
### Version-two wiring comparison helper
- SHA-256: `641c506ed41cefbce7a743ef93a90dbd11b963c6f6f89a249f44c4429c343995`.
- Scope: Uses independent prepared copies and typed projection equality.
- Revalidate the referenced file before relying on this identifier.
- A newer file with the same name must not inherit this proof.
### Historical crypto-short template
- SHA-256: `3e989e4fb5171a19ca48fb9865b463c28457a83e0641523ae75e4823f3aed1d4`.
- Scope: Do not confuse this with the current campaign template.
- Revalidate the referenced file before relying on this identifier.
- A newer file with the same name must not inherit this proof.
### Corrected quartet summary
- SHA-256: `36aea8f0d6cf57ab00279bc230c85b0dadfc540a9eecf1cb0c79ca9714e7414a`.
- Scope: All joint candidates were below baseline; conditional information remains useful.
- Revalidate the referenced file before relying on this identifier.
- A newer file with the same name must not inherit this proof.
### Actual fork-worker raw proof
- SHA-256: `eb2a9ac7eeb1c608c4460bc105a86884546a0d34cfc77702ac196004160c36fe`.
- Scope: Actual proof bytes preserved; smoke is not full production activation.
- Revalidate the referenced file before relying on this identifier.
- A newer file with the same name must not inherit this proof.
## 14. Known failed or incomplete attempts: preserve the lessons
- The KG probe returned an evaluation_error because its harness passed no canonical minimum-trades floor.
- This was not evidence that blanket-off trading failed; the raw evaluator validity was True.
- The failed probe remains preserved and must not be retimestamped as a later successful run.
- A full expanded Cartesian gzip on the Mac hit ENOSPC while being generated.
- Only that own failed generated partial was removed; frozen source templates were preserved.
- The campaign catalog was changed to a lossless factored row/header representation with deterministic lazy cell IDs.
- This is a local materialization issue, not evidence that the purchased 5T archive lacks capacity.
- The earlier source-independent template ordering validator omitted the writer default-group hoist.
- The corrected validator follows the actual writer rule; no template was resorted to make the check pass.
- A preservation task initially deleted compressed S6 duplicates before a requested full member-content check.
- The later full check passed, but the ordering error was acknowledged and is not a precedent to skip verification.
- A WD spreadsheet copy had one same-size checksum mismatch; only that file was corrected and the whole census rechecked.
- Same size is not content equality.
- TOSHIBA directory lookup stalled in filesystem I/O; no unmount or forced filesystem repair was performed.
- This does not remove the obligation to use the already archived 5T information.
- The old training sorter had high temporary disk use; a byte-equal alternative was proven separately.
- That proof was not silently substituted into the already running training job.
- Old progress outputs sometimes exposed mtime-based board dates; such dates cannot prove calculation vintage.
- Existing yellow summary sets lacked event identities and could not establish the 101-result threshold.
- The raw UNIUSDC progress-cell sample is promising indexed evidence, not a proven population count.
- The latest full-cell campaign had runtime readiness before any actual coverage cells were reported.
- Do not turn that readiness checkpoint into a claim that all four hosts were already calculating the new matrix.
## 15. Operational commands: read-only discovery and safe checks
The following commands are examples for inspecting the present workspace.
Verify paths and current state before using a historical launch command.
Do not run a second copy of a job merely because a previous observation timed out.
### Current pilot identity
```sh
sha256sum v15_pilot.py
```
On macOS use shasum -a 256 if sha256sum is unavailable.
### Host resolution
```sh
ssh -G s2
```
Inspect hostname, user, port, and configured identity locally; do not print private-key content.
### Remote hostname
```sh
ssh -o BatchMode=yes -o ConnectTimeout=10 s2 hostname
```
Verify the actual host before materializing anything.
### Archive browsing
```sh
sftp 5t
```
Use read-only listings first; this is a transfer service, not a general remote shell.
### Campaign files
```sh
rg --files data/reports/full_cell_campaign_20261031
```
Inspect completed manifests and current staging state.
### Current source locks
```sh
rg -n "v15_pilot.py|v12_quick_engine.py|ez_manage.py" LOCKED_FILES.md
```
Read the applicable entries before editing.
### Exact test selection
```sh
python3 -m pytest -q tests/test_v15_nextgen_protection_priority.py
```
A pass covers the selected unit tests only.
### Consumer tests
```sh
python3 -m pytest -q tests/test_v15_nextgen_pilot_consumer.py
```
Do not interpret extracted-callsite tests as a full fleet run.
### Retention tests
```sh
python3 -m pytest -q tests/test_v15_nextgen_knowledge_retention.py
```
A pass does not prove all archived sources were ingested.
### Location registry
```sh
python3 tools/data_locations.py --where brain_delta_log
```
Treat the registry as a lead, then verify actual current paths.
### Board calculation vintage
```sh
python3 tools/board_vintage_audit.py --help
```
Read current CLI before a report-only audit; never restamp old cells.
### S2 production gate
```sh
python3 ~/v15_bench/tools/v15_version_gate.py --help
```
Use the existing actual submit path for production, after the candidate is concrete and verified.
## 16. Evidence index: actual local files and snapshot hashes
The following entries are generated from existing local files while this handoff is built.
Each hash identifies bytes read at that moment.
A file may change later because agents continue working.
An entry is a pointer for verification, not a substitute for reading the actual receipt.
No financial metric is copied into this index.
File modification times are intentionally not used as calculation dates.
### Evidence 001: data/reports/nextgen_supervisor_status.json
- Local path: [data/reports/nextgen_supervisor_status.json](data/reports/nextgen_supervisor_status.json).
- SHA-256 at document construction: `c0808d16ad975b11b91092c142b9d4c7bea49201016bc96ec2b46f268b98063a`.
- Bytes read: 40937.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: active_not_achieved.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 002: data/reports/full_cell_campaign_20261031/operator_contract.json
- Local path: [data/reports/full_cell_campaign_20261031/operator_contract.json](data/reports/full_cell_campaign_20261031/operator_contract.json).
- SHA-256 at document construction: `4ff9dc3d1988d46bf1f0cd5fb5aba6987500278a75d1727a0761651f4d99845e`.
- Bytes read: 1567.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: full_cell_operator_contract/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 003: data/reports/5t_learning_source_catalog_20261031T0428Z.json
- Local path: [data/reports/5t_learning_source_catalog_20261031T0428Z.json](data/reports/5t_learning_source_catalog_20261031T0428Z.json).
- SHA-256 at document construction: `e2b6a9a3113734f172e6697195eb7c84f6d042364e33602c4544caffda366f4c`.
- Bytes read: 5290.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: 5t_learning_source_catalog/v1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 004: data/reports/learning_preservation_matrix_20261031T0309Z.json
- Local path: [data/reports/learning_preservation_matrix_20261031T0309Z.json](data/reports/learning_preservation_matrix_20261031T0309Z.json).
- SHA-256 at document construction: `e0d2596c937f3ea6d0d5fe1e789ef7b9452a21ab85b77b3ec027a81748f97651`.
- Bytes read: 39663.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 005: data/reports/learning_preservation_matrix_20261031T0420Z.json
- Local path: [data/reports/learning_preservation_matrix_20261031T0420Z.json](data/reports/learning_preservation_matrix_20261031T0420Z.json).
- SHA-256 at document construction: `d6e023a5d26ae182191af151535f8a528497b55ca4692b1a088a626fbd64eb9c`.
- Bytes read: 45312.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 006: data/reports/learning_preservation_matrix_20261031T0430Z.json
- Local path: [data/reports/learning_preservation_matrix_20261031T0430Z.json](data/reports/learning_preservation_matrix_20261031T0430Z.json).
- SHA-256 at document construction: `2b444b95ce1913dbf0407408af74ca21d620fa7ee010d8f49c56292d8d387326`.
- Bytes read: 46624.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 007: data/reports/nextgen_selected_effective_inventory_20261031.json
- Local path: [data/reports/nextgen_selected_effective_inventory_20261031.json](data/reports/nextgen_selected_effective_inventory_20261031.json).
- SHA-256 at document construction: `5aab026e63aceec78309920f5efebb8802d9f75f3aa1f03fb2631a3b5851494f`.
- Bytes read: 30632.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: selected_effective_field_inventory/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 008: data/reports/nextgen_selected_missing_fields_template_inventory_20261031.json
- Local path: [data/reports/nextgen_selected_missing_fields_template_inventory_20261031.json](data/reports/nextgen_selected_missing_fields_template_inventory_20261031.json).
- SHA-256 at document construction: `9edda6cbc013b04f5ec7860ad5a51eec9c14544ef6ed4cb28cae5fcaf53cc2bb`.
- Bytes read: 4087.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 009: data/reports/nextgen_profit_protection_ledger_inventory_20261031.json
- Local path: [data/reports/nextgen_profit_protection_ledger_inventory_20261031.json](data/reports/nextgen_profit_protection_ledger_inventory_20261031.json).
- SHA-256 at document construction: `8392055952dc60bdb1729658f73d026f8eb2f3acfe27a3d39a5720bb6ff3e3f5`.
- Bytes read: 5256.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: profit_protection_native_ledger_inventory/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 010: data/reports/nextgen_profit_protection_observed_fees_20261031.json
- Local path: [data/reports/nextgen_profit_protection_observed_fees_20261031.json](data/reports/nextgen_profit_protection_observed_fees_20261031.json).
- SHA-256 at document construction: `05af36db9de37fb1a7fd64cdf38b57fe55ca0a54080444860bee7e4bbf22c565`.
- Bytes read: 1189.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: observed_fill_fee_classes/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 011: data/reports/nextgen_profit_protection_replay_review_20261031.json
- Local path: [data/reports/nextgen_profit_protection_replay_review_20261031.json](data/reports/nextgen_profit_protection_replay_review_20261031.json).
- SHA-256 at document construction: `c8b897b0421633d94b47092534dfa61fa327c429be98b13900f4f2f39af40cb2`.
- Bytes read: 1102.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: profit_protection_replay_input_review/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 012: data/reports/nextgen_protection_activation_conflicts_20261031.json
- Local path: [data/reports/nextgen_protection_activation_conflicts_20261031.json](data/reports/nextgen_protection_activation_conflicts_20261031.json).
- SHA-256 at document construction: `91b358a044996dda27c854138ec4f9f5f95fa3c7487b8ef9e9a202e405b10df0`.
- Bytes read: 1715.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: protection_activation_conflict/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 013: data/reports/s5_b_events_sorted_gzip_receipt_20261031T030014Z.json
- Local path: [data/reports/s5_b_events_sorted_gzip_receipt_20261031T030014Z.json](data/reports/s5_b_events_sorted_gzip_receipt_20261031T030014Z.json).
- SHA-256 at document construction: `df3f4a07082e014cce17b89831e7197698a67f78b57aed6bd6937c1f4fa3950c`.
- Bytes read: 1646.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 014: data/reports/s6_ab_training_state_copy_receipt_20261031T024827Z.json
- Local path: [data/reports/s6_ab_training_state_copy_receipt_20261031T024827Z.json](data/reports/s6_ab_training_state_copy_receipt_20261031T024827Z.json).
- SHA-256 at document construction: `706dc39d8a63dc84bed99d5d57b165012d7fb60669f262d5996ec3de8457240a`.
- Bytes read: 2783.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 015: data/reports/s5_model_payload_copy_receipt_20261031T023652Z.json
- Local path: [data/reports/s5_model_payload_copy_receipt_20261031T023652Z.json](data/reports/s5_model_payload_copy_receipt_20261031T023652Z.json).
- SHA-256 at document construction: `93d7888b6c7e76b434247e0728e5b6ce6850efa1a37e7670df63032fe0c50c16`.
- Bytes read: 8813.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 016: data/reports/s5_must_unique_closure_20261031T024429Z.json
- Local path: [data/reports/s5_must_unique_closure_20261031T024429Z.json](data/reports/s5_must_unique_closure_20261031T024429Z.json).
- SHA-256 at document construction: `e5191e02906de12e72b2be5d49e044562b7c16f4f19512f16f8cc5e9a006959a`.
- Bytes read: 83218.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 017: data/reports/s5_symlink_targets_inventory_20261031T040354Z.json
- Local path: [data/reports/s5_symlink_targets_inventory_20261031T040354Z.json](data/reports/s5_symlink_targets_inventory_20261031T040354Z.json).
- SHA-256 at document construction: `3688187d03e25406ea1cebb75e5d24f1c6e85da55971274d811f7879bad896ea`.
- Bytes read: 4436.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 018: data/reports/s5_v15_365cycle_verified_copy_receipt_20261031T0425Z.json
- Local path: [data/reports/s5_v15_365cycle_verified_copy_receipt_20261031T0425Z.json](data/reports/s5_v15_365cycle_verified_copy_receipt_20261031T0425Z.json).
- SHA-256 at document construction: `62683b72b14f7b7273d64c87e5c9af077dcf3adae3c6b6c6ea5a22596988f7a2`.
- Bytes read: 1453.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 019: data/reports/nextgen_authority_repair_20261031/1INCHUSDT_SHORT_selected_effective_after_authority.json
- Local path: [data/reports/nextgen_authority_repair_20261031/1INCHUSDT_SHORT_selected_effective_after_authority.json](data/reports/nextgen_authority_repair_20261031/1INCHUSDT_SHORT_selected_effective_after_authority.json).
- SHA-256 at document construction: `c5c99cda6059dcfdac81097eeda26d74c64e924a4e5fb2ee2bcec16f19000b5e`.
- Bytes read: 16374.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 020: data/reports/nextgen_authority_repair_20261031/IBM_LONG_selected_effective_after_authority.json
- Local path: [data/reports/nextgen_authority_repair_20261031/IBM_LONG_selected_effective_after_authority.json](data/reports/nextgen_authority_repair_20261031/IBM_LONG_selected_effective_after_authority.json).
- SHA-256 at document construction: `fe730e45a72673c55af29d45a0f8063064a36bc54f4cccec604550ba994f8aa9`.
- Bytes read: 15941.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 021: data/reports/nextgen_authority_repair_20261031/actual_authority_summary.json
- Local path: [data/reports/nextgen_authority_repair_20261031/actual_authority_summary.json](data/reports/nextgen_authority_repair_20261031/actual_authority_summary.json).
- SHA-256 at document construction: `76e511e5c3aa7b4efc5737ec740058e6aa0d6ec7622c91940842d636359c7609`.
- Bytes read: 5329.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 022: data/reports/nextgen_authority_repair_20261031/actual_evidence/both/previous_baseline_oracle.json
- Local path: [data/reports/nextgen_authority_repair_20261031/actual_evidence/both/previous_baseline_oracle.json](data/reports/nextgen_authority_repair_20261031/actual_evidence/both/previous_baseline_oracle.json).
- SHA-256 at document construction: `328fd8c00b334c4b1d2e08a920c8a792c237f40d0159a1ddc25a8e3f1f964746`.
- Bytes read: 213391.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_authority_correction_diagnostic/1.
- Declared status: MEASURED_CODE_REPAIR_DIAGNOSTIC_NOT_NN_GAIN_OR_PRIOR_ORACLE.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 023: data/reports/nextgen_authority_repair_20261031/actual_evidence/both/previous_execution_ledger.json
- Local path: [data/reports/nextgen_authority_repair_20261031/actual_evidence/both/previous_execution_ledger.json](data/reports/nextgen_authority_repair_20261031/actual_evidence/both/previous_execution_ledger.json).
- SHA-256 at document construction: `f946a30a675fb48d49471bec061952bf42311cd119b48aeaf0439d254bd66ffe`.
- Bytes read: 59024.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 024: data/reports/nextgen_authority_repair_20261031/actual_evidence/crypto_fixed_true_v2/oracle.json
- Local path: [data/reports/nextgen_authority_repair_20261031/actual_evidence/crypto_fixed_true_v2/oracle.json](data/reports/nextgen_authority_repair_20261031/actual_evidence/crypto_fixed_true_v2/oracle.json).
- SHA-256 at document construction: `fc31eea39b219db7bffd2daaf5f5aee38c560c87a1df0410b62925063f47310c`.
- Bytes read: 408904.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_existing_switch_wiring_diagnostic/1.
- Declared status: MEASURED_WIRING_VARIANT_NOT_ACCEPTED_HISTORY_OR_NN_GAIN.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 025: data/reports/nextgen_authority_repair_20261031/actual_evidence/field_wiring_v2/crypto_both_true/oracle.json
- Local path: [data/reports/nextgen_authority_repair_20261031/actual_evidence/field_wiring_v2/crypto_both_true/oracle.json](data/reports/nextgen_authority_repair_20261031/actual_evidence/field_wiring_v2/crypto_both_true/oracle.json).
- SHA-256 at document construction: `bf3b6aeb96cbdae30649de30856e3337fb1b78fb6f4d9b04bcdf145cabaab145`.
- Bytes read: 408942.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_existing_switch_wiring_diagnostic/1.
- Declared status: MEASURED_WIRING_VARIANT_NOT_ACCEPTED_HISTORY_OR_NN_GAIN.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 026: data/reports/nextgen_authority_repair_20261031/actual_evidence/field_wiring_v2/crypto_fixed_true/oracle.json
- Local path: [data/reports/nextgen_authority_repair_20261031/actual_evidence/field_wiring_v2/crypto_fixed_true/oracle.json](data/reports/nextgen_authority_repair_20261031/actual_evidence/field_wiring_v2/crypto_fixed_true/oracle.json).
- SHA-256 at document construction: `fc31eea39b219db7bffd2daaf5f5aee38c560c87a1df0410b62925063f47310c`.
- Bytes read: 408904.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_existing_switch_wiring_diagnostic/1.
- Declared status: MEASURED_WIRING_VARIANT_NOT_ACCEPTED_HISTORY_OR_NN_GAIN.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 027: data/reports/nextgen_authority_repair_20261031/actual_evidence/field_wiring_v2/crypto_momentum_true/oracle.json
- Local path: [data/reports/nextgen_authority_repair_20261031/actual_evidence/field_wiring_v2/crypto_momentum_true/oracle.json](data/reports/nextgen_authority_repair_20261031/actual_evidence/field_wiring_v2/crypto_momentum_true/oracle.json).
- SHA-256 at document construction: `4f3e44784e8c62d5872f0e2eef1aedefa18b0c6d9a426c65959ade99c72360f9`.
- Bytes read: 408909.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_existing_switch_wiring_diagnostic/1.
- Declared status: MEASURED_WIRING_VARIANT_NOT_ACCEPTED_HISTORY_OR_NN_GAIN.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 028: data/reports/nextgen_authority_repair_20261031/actual_evidence/field_wiring_v2/ibm_both_true/oracle.json
- Local path: [data/reports/nextgen_authority_repair_20261031/actual_evidence/field_wiring_v2/ibm_both_true/oracle.json](data/reports/nextgen_authority_repair_20261031/actual_evidence/field_wiring_v2/ibm_both_true/oracle.json).
- SHA-256 at document construction: `3658b8887daa0a088528b284e9bc2d9fec8aabdf874ebb9486ff083ff1a06ad6`.
- Bytes read: 409715.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_existing_switch_wiring_diagnostic/1.
- Declared status: MEASURED_WIRING_VARIANT_NOT_ACCEPTED_HISTORY_OR_NN_GAIN.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 029: data/reports/nextgen_authority_repair_20261031/actual_evidence/field_wiring_v2/ibm_fixed_true/oracle.json
- Local path: [data/reports/nextgen_authority_repair_20261031/actual_evidence/field_wiring_v2/ibm_fixed_true/oracle.json](data/reports/nextgen_authority_repair_20261031/actual_evidence/field_wiring_v2/ibm_fixed_true/oracle.json).
- SHA-256 at document construction: `81f4dcc3ad3f240642e4434801f87b6f0d8cd81cb7edae4ee63241d572f5b4bd`.
- Bytes read: 409677.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_existing_switch_wiring_diagnostic/1.
- Declared status: MEASURED_WIRING_VARIANT_NOT_ACCEPTED_HISTORY_OR_NN_GAIN.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 030: data/reports/nextgen_authority_repair_20261031/actual_evidence/field_wiring_v2/ibm_momentum_true/oracle.json
- Local path: [data/reports/nextgen_authority_repair_20261031/actual_evidence/field_wiring_v2/ibm_momentum_true/oracle.json](data/reports/nextgen_authority_repair_20261031/actual_evidence/field_wiring_v2/ibm_momentum_true/oracle.json).
- SHA-256 at document construction: `d4c67e9ae05a3cfe1c07ab14344cc4e15999c6ec1941957aaa13741a8b78d75c`.
- Bytes read: 409682.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_existing_switch_wiring_diagnostic/1.
- Declared status: MEASURED_WIRING_VARIANT_NOT_ACCEPTED_HISTORY_OR_NN_GAIN.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 031: data/reports/nextgen_authority_repair_20261031/actual_evidence/ibm_both/previous_baseline_oracle.json
- Local path: [data/reports/nextgen_authority_repair_20261031/actual_evidence/ibm_both/previous_baseline_oracle.json](data/reports/nextgen_authority_repair_20261031/actual_evidence/ibm_both/previous_baseline_oracle.json).
- SHA-256 at document construction: `ff5e5899ffba315638fd78c24e6840911c2b3d6c13b84c46b620b4e7c5f517b4`.
- Bytes read: 212721.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_authority_correction_diagnostic/1.
- Declared status: MEASURED_CODE_REPAIR_DIAGNOSTIC_NOT_NN_GAIN_OR_PRIOR_ORACLE.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 032: data/reports/nextgen_authority_repair_20261031/actual_evidence/ibm_both/previous_execution_ledger.json
- Local path: [data/reports/nextgen_authority_repair_20261031/actual_evidence/ibm_both/previous_execution_ledger.json](data/reports/nextgen_authority_repair_20261031/actual_evidence/ibm_both/previous_execution_ledger.json).
- SHA-256 at document construction: `7829b5ae490811c4b291777171c388f6b25c1154ebd91567c8839dece9a7d018`.
- Bytes read: 34818.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 033: data/reports/nextgen_authority_repair_20261031/actual_evidence/ibm_old/previous_baseline_oracle.json
- Local path: [data/reports/nextgen_authority_repair_20261031/actual_evidence/ibm_old/previous_baseline_oracle.json](data/reports/nextgen_authority_repair_20261031/actual_evidence/ibm_old/previous_baseline_oracle.json).
- SHA-256 at document construction: `09a1eaa53235abffab7f1d2f9f91dcd29734193e65a33222dbee599449d25fac`.
- Bytes read: 206910.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_previous_baseline_oracle/1.
- Declared status: MEASURED_PREVIOUS_BASELINE_ONLY_NOT_CANDIDATE_PARITY.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 034: data/reports/nextgen_authority_repair_20261031/actual_evidence/ibm_old/previous_execution_ledger.json
- Local path: [data/reports/nextgen_authority_repair_20261031/actual_evidence/ibm_old/previous_execution_ledger.json](data/reports/nextgen_authority_repair_20261031/actual_evidence/ibm_old/previous_execution_ledger.json).
- SHA-256 at document construction: `30d45f69a44dca9f5df09335eca055aa0d198a555f60e58c5837b9183db535d9`.
- Bytes read: 21749.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 035: data/reports/nextgen_authority_repair_20261031/actual_evidence/layer_only/previous_baseline_oracle.json
- Local path: [data/reports/nextgen_authority_repair_20261031/actual_evidence/layer_only/previous_baseline_oracle.json](data/reports/nextgen_authority_repair_20261031/actual_evidence/layer_only/previous_baseline_oracle.json).
- SHA-256 at document construction: `a29daafdb15980c556e902e02ed6fee147a58b160a131311f5c13b7eb0be16d8`.
- Bytes read: 213403.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_authority_correction_diagnostic/1.
- Declared status: MEASURED_CODE_REPAIR_DIAGNOSTIC_NOT_NN_GAIN_OR_PRIOR_ORACLE.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 036: data/reports/nextgen_authority_repair_20261031/actual_evidence/layer_only/previous_execution_ledger.json
- Local path: [data/reports/nextgen_authority_repair_20261031/actual_evidence/layer_only/previous_execution_ledger.json](data/reports/nextgen_authority_repair_20261031/actual_evidence/layer_only/previous_execution_ledger.json).
- SHA-256 at document construction: `f0d8b62c0030b0e8adce371673c22f513d0223c3df222221bbcade0277375162`.
- Bytes read: 52722.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 037: data/reports/nextgen_authority_repair_20261031/actual_evidence/old_reference/previous_baseline_oracle.json
- Local path: [data/reports/nextgen_authority_repair_20261031/actual_evidence/old_reference/previous_baseline_oracle.json](data/reports/nextgen_authority_repair_20261031/actual_evidence/old_reference/previous_baseline_oracle.json).
- SHA-256 at document construction: `0a822a68a84275b6b1c71c7e38c005c55c0e65a25b3d70c026450f296bb8591a`.
- Bytes read: 206247.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_previous_baseline_oracle/1.
- Declared status: MEASURED_PREVIOUS_BASELINE_ONLY_NOT_CANDIDATE_PARITY.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 038: data/reports/nextgen_authority_repair_20261031/actual_evidence/old_reference/previous_execution_ledger.json
- Local path: [data/reports/nextgen_authority_repair_20261031/actual_evidence/old_reference/previous_execution_ledger.json](data/reports/nextgen_authority_repair_20261031/actual_evidence/old_reference/previous_execution_ledger.json).
- SHA-256 at document construction: `2042c4558e106ed995a78b0d468f2c4a1bb3bf8b2c3750a4e91c9fa1a9eede6d`.
- Bytes read: 31172.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 039: data/reports/nextgen_authority_repair_20261031/actual_evidence/orphan_only/previous_baseline_oracle.json
- Local path: [data/reports/nextgen_authority_repair_20261031/actual_evidence/orphan_only/previous_baseline_oracle.json](data/reports/nextgen_authority_repair_20261031/actual_evidence/orphan_only/previous_baseline_oracle.json).
- SHA-256 at document construction: `841d202337e508b2e5d7214ca58f7f0a27a1c6e9c32dcc40de895b7468075415`.
- Bytes read: 207565.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_authority_correction_diagnostic/1.
- Declared status: MEASURED_CODE_REPAIR_DIAGNOSTIC_NOT_NN_GAIN_OR_PRIOR_ORACLE.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 040: data/reports/nextgen_authority_repair_20261031/actual_evidence/orphan_only/previous_execution_ledger.json
- Local path: [data/reports/nextgen_authority_repair_20261031/actual_evidence/orphan_only/previous_execution_ledger.json](data/reports/nextgen_authority_repair_20261031/actual_evidence/orphan_only/previous_execution_ledger.json).
- SHA-256 at document construction: `cc47fdb253b85e6e921cd2c90a6cf3c883bafa110af8a9447058384f1e02e510`.
- Bytes read: 34710.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 041: data/reports/nextgen_authority_repair_20261031/actual_evidence_index.json
- Local path: [data/reports/nextgen_authority_repair_20261031/actual_evidence_index.json](data/reports/nextgen_authority_repair_20261031/actual_evidence_index.json).
- SHA-256 at document construction: `1877b9233ad36371f03a0297078e8c8b9ab140a777e840492ca5e1e0dfc66304`.
- Bytes read: 2280.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 042: data/reports/nextgen_authority_repair_20261031/current_forward_review1/linux_test_receipt.json
- Local path: [data/reports/nextgen_authority_repair_20261031/current_forward_review1/linux_test_receipt.json](data/reports/nextgen_authority_repair_20261031/current_forward_review1/linux_test_receipt.json).
- SHA-256 at document construction: `5a7ccff681b76e8e74df0d05c75f12cb2012def771ce409cda67d027d98ea3a0`.
- Bytes read: 517.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 043: data/reports/nextgen_authority_repair_20261031/current_forward_review1/manifest.json
- Local path: [data/reports/nextgen_authority_repair_20261031/current_forward_review1/manifest.json](data/reports/nextgen_authority_repair_20261031/current_forward_review1/manifest.json).
- SHA-256 at document construction: `c22fa046b750918eb12f1dbc22646557e885b32b609297adf65243328538e14c`.
- Bytes read: 2192.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: REVIEW_ONLY_NO_CANONICAL_MUTATION_NO_CURRENT_ENGINE_MARKET_EVALUATION.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 044: data/reports/nextgen_authority_repair_20261031/current_forward_review1/snapshot_receipt.json
- Local path: [data/reports/nextgen_authority_repair_20261031/current_forward_review1/snapshot_receipt.json](data/reports/nextgen_authority_repair_20261031/current_forward_review1/snapshot_receipt.json).
- SHA-256 at document construction: `13db0810925023bd1658c88cdcf84eb739f827bb2b56c21e8686b6edc45f59d3`.
- Bytes read: 729.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 045: data/reports/nextgen_authority_repair_20261031/field_defaults_current_inventory.json
- Local path: [data/reports/nextgen_authority_repair_20261031/field_defaults_current_inventory.json](data/reports/nextgen_authority_repair_20261031/field_defaults_current_inventory.json).
- SHA-256 at document construction: `d6fa194525c1f18e5a049dcbe32128397a7e0208ac19fb1dc339b6abe6fd01e5`.
- Bytes read: 5483.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 046: data/reports/nextgen_authority_repair_20261031/field_propagation_review1/actual_v2_summary.json
- Local path: [data/reports/nextgen_authority_repair_20261031/field_propagation_review1/actual_v2_summary.json](data/reports/nextgen_authority_repair_20261031/field_propagation_review1/actual_v2_summary.json).
- SHA-256 at document construction: `397fa08ea68c5f44627be469d15516dc1967cac24bd71e9610207b6020018b8f`.
- Bytes read: 126351.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 047: data/reports/nextgen_authority_repair_20261031/field_propagation_review1/kg_preparation_receipt.json
- Local path: [data/reports/nextgen_authority_repair_20261031/field_propagation_review1/kg_preparation_receipt.json](data/reports/nextgen_authority_repair_20261031/field_propagation_review1/kg_preparation_receipt.json).
- SHA-256 at document construction: `baf8c1598310c6b4a4f58c836aa7d77bf3a04155da7afb9c0d997941a7afc938`.
- Bytes read: 1066.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: PREPARED_KG_SOURCE_HELPER_DIAGNOSTIC_NO_ENGINE_EXECUTED.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 048: data/reports/nextgen_authority_repair_20261031/field_propagation_review1/live_vector_authority_mapping.json
- Local path: [data/reports/nextgen_authority_repair_20261031/field_propagation_review1/live_vector_authority_mapping.json](data/reports/nextgen_authority_repair_20261031/field_propagation_review1/live_vector_authority_mapping.json).
- SHA-256 at document construction: `2d90b33832a9717b99ab38605a9900eef9846767532055aceae55da749ff8bfd`.
- Bytes read: 2599.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 049: data/reports/nextgen_authority_repair_20261031/field_propagation_review1/manifest.json
- Local path: [data/reports/nextgen_authority_repair_20261031/field_propagation_review1/manifest.json](data/reports/nextgen_authority_repair_20261031/field_propagation_review1/manifest.json).
- SHA-256 at document construction: `deda0efff07116e1e7d6a61a21e92061a097acb5d2228827f65230a7f8c54878`.
- Bytes read: 1401.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: STAGED_LINUX_SOURCE_UNITS_PASS_NO_ACTUAL_FIELD_ENGINE_EVALUATION.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 050: data/reports/nextgen_authority_repair_20261031/field_propagation_review1/preparation_receipt_v2.json
- Local path: [data/reports/nextgen_authority_repair_20261031/field_propagation_review1/preparation_receipt_v2.json](data/reports/nextgen_authority_repair_20261031/field_propagation_review1/preparation_receipt_v2.json).
- SHA-256 at document construction: `3f61775f998d1316f237f804f78fe8cae7350e3dba7b017943981b63d80ba8f0`.
- Bytes read: 2396.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: PREPARED_V2_NO_ENGINE_EXECUTION.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 051: data/reports/nextgen_authority_repair_20261031/field_propagation_review1/s6_crypto_preparation_receipt.json
- Local path: [data/reports/nextgen_authority_repair_20261031/field_propagation_review1/s6_crypto_preparation_receipt.json](data/reports/nextgen_authority_repair_20261031/field_propagation_review1/s6_crypto_preparation_receipt.json).
- SHA-256 at document construction: `aba2fe74c1d15e631955639fac03834f9e194dc29f86dd30547fc2e4b65cf751`.
- Bytes read: 2102.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: PREPARED_FIELD_WIRING_VARIANTS_NO_ENGINE_EXECUTION.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 052: data/reports/nextgen_authority_repair_20261031/field_propagation_review1/s6_ibm_preparation_receipt.json
- Local path: [data/reports/nextgen_authority_repair_20261031/field_propagation_review1/s6_ibm_preparation_receipt.json](data/reports/nextgen_authority_repair_20261031/field_propagation_review1/s6_ibm_preparation_receipt.json).
- SHA-256 at document construction: `6c8a92c9ac41ddfec92164b6880f29f63cea30b0385296f493b8eab360f4c5d2`.
- Bytes read: 2083.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: PREPARED_FIELD_WIRING_VARIANTS_NO_ENGINE_EXECUTION.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 053: data/reports/nextgen_authority_repair_20261031/field_propagation_review1/v2_actual_evidence_manifest.json
- Local path: [data/reports/nextgen_authority_repair_20261031/field_propagation_review1/v2_actual_evidence_manifest.json](data/reports/nextgen_authority_repair_20261031/field_propagation_review1/v2_actual_evidence_manifest.json).
- SHA-256 at document construction: `e9ba2b21fe3a705ed2cec38ef30aff0d7389a5fc82107a2e48daa010dab3d74a`.
- Bytes read: 2146.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 054: data/reports/nextgen_authority_repair_20261031/field_propagation_review1/v2_probe_test_receipt.json
- Local path: [data/reports/nextgen_authority_repair_20261031/field_propagation_review1/v2_probe_test_receipt.json](data/reports/nextgen_authority_repair_20261031/field_propagation_review1/v2_probe_test_receipt.json).
- SHA-256 at document construction: `ee68cfc8ebda6bbfde26c924f9567bc5b8c25d6773f1acdf029943a2e6b9b69d`.
- Bytes read: 1921.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: FIVE_FOCUSED_SYNTHETIC_TESTS_PASS_NO_ENGINE_LAUNCHED.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 055: data/reports/nextgen_authority_repair_20261031/live_fixed_quantity_review1/manifest.json
- Local path: [data/reports/nextgen_authority_repair_20261031/live_fixed_quantity_review1/manifest.json](data/reports/nextgen_authority_repair_20261031/live_fixed_quantity_review1/manifest.json).
- SHA-256 at document construction: `0c007cf0f7d231593d87a6ac971401b49ff72cd6768ceacde6bfca9bb50c5c74`.
- Bytes read: 703.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: REVIEW_ONLY_NO_CANONICAL_EDIT_OR_DEPLOY.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 056: data/reports/nextgen_authority_repair_20261031/live_fixed_quantity_review1/test_receipt.json
- Local path: [data/reports/nextgen_authority_repair_20261031/live_fixed_quantity_review1/test_receipt.json](data/reports/nextgen_authority_repair_20261031/live_fixed_quantity_review1/test_receipt.json).
- SHA-256 at document construction: `7bec507b7175b054c6f4d70aa0a6d2c0276386ba57b1fb5266c636657672a5ca`.
- Bytes read: 465.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 057: data/reports/nextgen_authority_repair_20261031/manifest.json
- Local path: [data/reports/nextgen_authority_repair_20261031/manifest.json](data/reports/nextgen_authority_repair_20261031/manifest.json).
- SHA-256 at document construction: `db36ec262de4ed2027681e4211798be48326ac23b1c9cfdc66417bae46414fb8`.
- Bytes read: 3413.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: REVIEW_SOURCE_PLUS_SEPARATE_MEASURED_OLDCORE_AUTHORITY_ARMS_NO_DEPLOYMENT.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 058: data/reports/nextgen_authority_repair_20261031/s6_preparation_receipt.json
- Local path: [data/reports/nextgen_authority_repair_20261031/s6_preparation_receipt.json](data/reports/nextgen_authority_repair_20261031/s6_preparation_receipt.json).
- SHA-256 at document construction: `76f711ec00e7917d8a91b08dc8773764233a8f87646defe1a844e0a13f08d2e4`.
- Bytes read: 3445.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: PREPARED_THREE_AUTHORITY_ARMS_NO_ENGINE_EXECUTION.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 059: data/reports/nextgen_authority_repair_20261031/test_receipt.json
- Local path: [data/reports/nextgen_authority_repair_20261031/test_receipt.json](data/reports/nextgen_authority_repair_20261031/test_receipt.json).
- SHA-256 at document construction: `27967d2b263b366da0a0827f78529037443bf24669ab44a1e6ad5025b2922981`.
- Bytes read: 1459.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 060: data/reports/nextgen_consumer_integration_20261031/actual_fork_smoke_01/actual_fork_smoke.json
- Local path: [data/reports/nextgen_consumer_integration_20261031/actual_fork_smoke_01/actual_fork_smoke.json](data/reports/nextgen_consumer_integration_20261031/actual_fork_smoke_01/actual_fork_smoke.json).
- SHA-256 at document construction: `6dbef23d6b46ab36a008a6bc113e3f0b362fbd058937ea13bb28646ea3b30581`.
- Bytes read: 4560.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: nextgen_actual_fork_smoke/1.
- Declared status: actual_fork_worker_baseline_parity_pass.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 061: data/reports/nextgen_consumer_integration_20261031/actual_fork_smoke_01/corrected_authority_bootstrap.json
- Local path: [data/reports/nextgen_consumer_integration_20261031/actual_fork_smoke_01/corrected_authority_bootstrap.json](data/reports/nextgen_consumer_integration_20261031/actual_fork_smoke_01/corrected_authority_bootstrap.json).
- SHA-256 at document construction: `8787d114ae4d8b494e6a940cceb25aff631cb7f2f8897ba533a9d0c3f1db256e`.
- Bytes read: 912.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: corrected_authority_quartet_bootstrap/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 062: data/reports/nextgen_consumer_integration_20261031/actual_fork_smoke_01/oracle_bootstrap_request.json
- Local path: [data/reports/nextgen_consumer_integration_20261031/actual_fork_smoke_01/oracle_bootstrap_request.json](data/reports/nextgen_consumer_integration_20261031/actual_fork_smoke_01/oracle_bootstrap_request.json).
- SHA-256 at document construction: `b97f5236cd63698070853f918067640e421ceeaadd3d5800ea2a1c4a1c1941b0`.
- Bytes read: 3100.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_prior_baseline_request/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 063: data/reports/nextgen_consumer_integration_20261031/actual_fork_smoke_01/prepared_env.json
- Local path: [data/reports/nextgen_consumer_integration_20261031/actual_fork_smoke_01/prepared_env.json](data/reports/nextgen_consumer_integration_20261031/actual_fork_smoke_01/prepared_env.json).
- SHA-256 at document construction: `20aebdc1064df92a0c2c5068d819ff65e9938c504b2b370437dce131eac87943`.
- Bytes read: 5057.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 064: data/reports/nextgen_consumer_integration_20261031/actual_fork_smoke_01/previous_baseline_oracle.json
- Local path: [data/reports/nextgen_consumer_integration_20261031/actual_fork_smoke_01/previous_baseline_oracle.json](data/reports/nextgen_consumer_integration_20261031/actual_fork_smoke_01/previous_baseline_oracle.json).
- SHA-256 at document construction: `77ce5e41e473c82cd85012a7f8fa894a1c6353b34281971fe86027be726dac35`.
- Bytes read: 206279.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_previous_baseline_oracle/1.
- Declared status: MEASURED_PREVIOUS_BASELINE_ONLY_NOT_CANDIDATE_PARITY.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 065: data/reports/nextgen_consumer_integration_20261031/actual_fork_smoke_01/previous_execution_ledger.json
- Local path: [data/reports/nextgen_consumer_integration_20261031/actual_fork_smoke_01/previous_execution_ledger.json](data/reports/nextgen_consumer_integration_20261031/actual_fork_smoke_01/previous_execution_ledger.json).
- SHA-256 at document construction: `f946a30a675fb48d49471bec061952bf42311cd119b48aeaf0439d254bd66ffe`.
- Bytes read: 59024.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 066: data/reports/nextgen_consumer_integration_20261031/actual_fork_smoke_01/previous_raw_capture_20261031T040344400448Z.provenance.json
- Local path: [data/reports/nextgen_consumer_integration_20261031/actual_fork_smoke_01/previous_raw_capture_20261031T040344400448Z.provenance.json](data/reports/nextgen_consumer_integration_20261031/actual_fork_smoke_01/previous_raw_capture_20261031T040344400448Z.provenance.json).
- SHA-256 at document construction: `50031871cfc5a3170ac39ac4de71547a84ca53730c04db89e9b41fdbdf5c77b7`.
- Bytes read: 196873.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 067: data/reports/nextgen_consumer_integration_20261031/actual_fork_smoke_01/worker_evidence/6da197e310f62ceaa212f62b98c83f8469e19100fa8555bedb9f4161563b5b4d_dcfb38dc87734489ad127139cefc109b.json
- Local path: [data/reports/nextgen_consumer_integration_20261031/actual_fork_smoke_01/worker_evidence/6da197e310f62ceaa212f62b98c83f8469e19100fa8555bedb9f4161563b5b4d_dcfb38dc87734489ad127139cefc109b.json](data/reports/nextgen_consumer_integration_20261031/actual_fork_smoke_01/worker_evidence/6da197e310f62ceaa212f62b98c83f8469e19100fa8555bedb9f4161563b5b4d_dcfb38dc87734489ad127139cefc109b.json).
- SHA-256 at document construction: `eb2a9ac7eeb1c608c4460bc105a86884546a0d34cfc77702ac196004160c36fe`.
- Bytes read: 530822.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: nextgen_actual_worker_measurement/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 068: data/reports/nextgen_consumer_integration_20261031/factory_revision_02/source_receipt.json
- Local path: [data/reports/nextgen_consumer_integration_20261031/factory_revision_02/source_receipt.json](data/reports/nextgen_consumer_integration_20261031/factory_revision_02/source_receipt.json).
- SHA-256 at document construction: `8536447d679e9cc0af61ae4f4cbcbb99f2bc9feb7e38bb0da5c04f288afe1a50`.
- Bytes read: 535.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: nextgen_factory_composition/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 069: data/reports/nextgen_consumer_integration_20261031/frozen_20261031T035743Z/data/reports/nextgen_consumer_integration_20261031/source_receipt.json
- Local path: [data/reports/nextgen_consumer_integration_20261031/frozen_20261031T035743Z/data/reports/nextgen_consumer_integration_20261031/source_receipt.json](data/reports/nextgen_consumer_integration_20261031/frozen_20261031T035743Z/data/reports/nextgen_consumer_integration_20261031/source_receipt.json).
- SHA-256 at document construction: `da698b952e0050364ce21414a12d3a2e431756bc66b7217cda800ff7023ab57a`.
- Bytes read: 3169.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: nextgen_consumer_review_patch/1.
- Declared status: review_only_actual_callsite_hooks_and_proof_producers_implemented_not_executed.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 070: data/reports/nextgen_consumer_integration_20261031/frozen_20261031T035743Z/frozen_receipt.json
- Local path: [data/reports/nextgen_consumer_integration_20261031/frozen_20261031T035743Z/frozen_receipt.json](data/reports/nextgen_consumer_integration_20261031/frozen_20261031T035743Z/frozen_receipt.json).
- SHA-256 at document construction: `cd2764a8551186f6993af052f5df6ec6a8dfa2e5a528ca177daf6545bace136d`.
- Bytes read: 1195.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 071: data/reports/nextgen_consumer_integration_20261031/proof_storage_comparison_01/comparison.json
- Local path: [data/reports/nextgen_consumer_integration_20261031/proof_storage_comparison_01/comparison.json](data/reports/nextgen_consumer_integration_20261031/proof_storage_comparison_01/comparison.json).
- SHA-256 at document construction: `a447ffa99d58acc58b52eabb5b6acbd865284613aa86b7dd7fb7f948b6f088d3`.
- Bytes read: 1252.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: lossless_actual_proof_storage_comparison/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 072: data/reports/nextgen_consumer_integration_20261031/proof_storage_comparison_01/records/eb2a9ac7eeb1c608c4460bc105a86884546a0d34cfc77702ac196004160c36fe.json
- Local path: [data/reports/nextgen_consumer_integration_20261031/proof_storage_comparison_01/records/eb2a9ac7eeb1c608c4460bc105a86884546a0d34cfc77702ac196004160c36fe.json](data/reports/nextgen_consumer_integration_20261031/proof_storage_comparison_01/records/eb2a9ac7eeb1c608c4460bc105a86884546a0d34cfc77702ac196004160c36fe.json).
- SHA-256 at document construction: `96e739f40d6a378b4477301074e240b012accb8ced96ee5a45b884f04dad480d`.
- Bytes read: 6809.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: nextgen_lossless_worker_proof/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 073: data/reports/nextgen_consumer_integration_20261031/review_contract.json
- Local path: [data/reports/nextgen_consumer_integration_20261031/review_contract.json](data/reports/nextgen_consumer_integration_20261031/review_contract.json).
- SHA-256 at document construction: `2192ae2952469879e555c46a19e7b9abaf475384d904a0633154297952678549`.
- Bytes read: 1797.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: nextgen_consumer_integration_requirements/1.
- Declared status: isolated_review_only_no_engine_run_or_deployment.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 074: data/reports/nextgen_consumer_integration_20261031/runtime_factory_smoke_01/inputs/domain_proof.json
- Local path: [data/reports/nextgen_consumer_integration_20261031/runtime_factory_smoke_01/inputs/domain_proof.json](data/reports/nextgen_consumer_integration_20261031/runtime_factory_smoke_01/inputs/domain_proof.json).
- SHA-256 at document construction: `844f630d4e2dc341e30244e0b449a2759a361259e2e132f6ffe899503ed6d392`.
- Bytes read: 4637.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: historical_protection_quartet_plan/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 075: data/reports/nextgen_consumer_integration_20261031/runtime_factory_smoke_01/source_receipt.json
- Local path: [data/reports/nextgen_consumer_integration_20261031/runtime_factory_smoke_01/source_receipt.json](data/reports/nextgen_consumer_integration_20261031/runtime_factory_smoke_01/source_receipt.json).
- SHA-256 at document construction: `73aadc57d82ec7411f5443ca488f412cfa2f071596b9aa8141e2eec2da555471`.
- Bytes read: 1767.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: stage_only_not_executed.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 076: data/reports/nextgen_consumer_integration_20261031/source_receipt.json
- Local path: [data/reports/nextgen_consumer_integration_20261031/source_receipt.json](data/reports/nextgen_consumer_integration_20261031/source_receipt.json).
- SHA-256 at document construction: `da698b952e0050364ce21414a12d3a2e431756bc66b7217cda800ff7023ab57a`.
- Bytes read: 3169.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: nextgen_consumer_review_patch/1.
- Declared status: review_only_actual_callsite_hooks_and_proof_producers_implemented_not_executed.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 077: data/reports/nextgen_consumer_integration_20261031/template_order_reconciliation_01.json
- Local path: [data/reports/nextgen_consumer_integration_20261031/template_order_reconciliation_01.json](data/reports/nextgen_consumer_integration_20261031/template_order_reconciliation_01.json).
- SHA-256 at document construction: `9d16a5283f331a94ab6914fbf1092e923df21e4391c5799728ed93a13b0bb44a`.
- Bytes read: 583853.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: verified_against_pinned_writer_rule_without_resort.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 078: data/reports/nextgen_pinned_baseline_review_20261030/candidate_assets_20261031T010658Z/manifest.json
- Local path: [data/reports/nextgen_pinned_baseline_review_20261030/candidate_assets_20261031T010658Z/manifest.json](data/reports/nextgen_pinned_baseline_review_20261030/candidate_assets_20261031T010658Z/manifest.json).
- SHA-256 at document construction: `9601746fc07c99ac7c2fcf7f7036c2dc78775a94bfe2d9b5e129870423934001`.
- Bytes read: 550.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 079: data/reports/nextgen_pinned_baseline_review_20261030/local_apply_receipt.json
- Local path: [data/reports/nextgen_pinned_baseline_review_20261030/local_apply_receipt.json](data/reports/nextgen_pinned_baseline_review_20261030/local_apply_receipt.json).
- SHA-256 at document construction: `56f851aa3c87e18c33acec2449ec3f6d7b9d45787079c2dbbaa34161ef80d911`.
- Bytes read: 597.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: LOCAL_FORWARD_PATCH_APPLIED_NOT_DEPLOYED.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 080: data/reports/nextgen_pinned_baseline_review_20261030/manifest.json
- Local path: [data/reports/nextgen_pinned_baseline_review_20261030/manifest.json](data/reports/nextgen_pinned_baseline_review_20261030/manifest.json).
- SHA-256 at document construction: `d6c06901da6a5f9447042224790469dabbb3edfc6a7ce7dc18ec30d7bba03cbe`.
- Bytes read: 2825.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: REVIEW_ONLY_ACTUAL_ONE_SIDE_BASELINE_PARITY_NO_GAIN_OR_PROMOTION_PROOF.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 081: data/reports/nextgen_pinned_baseline_review_20261030/producer_review/manifest.json
- Local path: [data/reports/nextgen_pinned_baseline_review_20261030/producer_review/manifest.json](data/reports/nextgen_pinned_baseline_review_20261030/producer_review/manifest.json).
- SHA-256 at document construction: `676799d3243efd6d983d51dd5318544f21ffe19549f5f98d7adcead4f951feaf`.
- Bytes read: 1784.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: INCOMPLETE_REVIEW_ONLY_DO_NOT_EXECUTE_OR_DEPLOY.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 082: data/reports/nextgen_pinned_baseline_review_20261030/protection_trace_actual_analysis.json
- Local path: [data/reports/nextgen_pinned_baseline_review_20261030/protection_trace_actual_analysis.json](data/reports/nextgen_pinned_baseline_review_20261030/protection_trace_actual_analysis.json).
- SHA-256 at document construction: `7670e921cda88d1b1f350bd29dc7c668787fdfb1ee8fa2c2dd0c41eb8b0c47c5`.
- Bytes read: 2435.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 083: data/reports/nextgen_pinned_baseline_review_20261030/protection_trace_netgate_actual_analysis.json
- Local path: [data/reports/nextgen_pinned_baseline_review_20261030/protection_trace_netgate_actual_analysis.json](data/reports/nextgen_pinned_baseline_review_20261030/protection_trace_netgate_actual_analysis.json).
- SHA-256 at document construction: `9f546c26dedb05b42cb9eadf95544a652d95250b4c368c3c6ab6157841c697e5`.
- Bytes read: 2040.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 084: data/reports/nextgen_pinned_baseline_review_20261030/protection_trace_netgate_preparation_s6_020004.json
- Local path: [data/reports/nextgen_pinned_baseline_review_20261030/protection_trace_netgate_preparation_s6_020004.json](data/reports/nextgen_pinned_baseline_review_20261030/protection_trace_netgate_preparation_s6_020004.json).
- SHA-256 at document construction: `e58f9fe67d0ddb057b493a2762d3cc33958a0ee2683aaa4e684a3377950fafc3`.
- Bytes read: 1381.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: PREPARED_NO_ENGINE_EXECUTION.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 085: data/reports/nextgen_pinned_baseline_review_20261030/protection_trace_preparation_s6_015107.json
- Local path: [data/reports/nextgen_pinned_baseline_review_20261030/protection_trace_preparation_s6_015107.json](data/reports/nextgen_pinned_baseline_review_20261030/protection_trace_preparation_s6_015107.json).
- SHA-256 at document construction: `0c67d77169722c3d058a8688195ce7168d01a0727ceda00e8d02ca00402c8706`.
- Bytes read: 1269.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: PREPARED_NO_ENGINE_EXECUTION.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 086: data/reports/nextgen_pinned_baseline_review_20261030/protection_trace_review.json
- Local path: [data/reports/nextgen_pinned_baseline_review_20261030/protection_trace_review.json](data/reports/nextgen_pinned_baseline_review_20261030/protection_trace_review.json).
- SHA-256 at document construction: `66f0290bb426876fb5d9cbaa6d2c60404a37f981f9f08162100e76224067fc64`.
- Bytes read: 1313.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: S6_PREPARED_HASH_VERIFIED_NO_ENGINE_EXECUTION.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 087: data/reports/nextgen_pinned_baseline_review_20261030/s2_candidate_attempt01/baseline_pin.json
- Local path: [data/reports/nextgen_pinned_baseline_review_20261030/s2_candidate_attempt01/baseline_pin.json](data/reports/nextgen_pinned_baseline_review_20261030/s2_candidate_attempt01/baseline_pin.json).
- SHA-256 at document construction: `429102aa89fdf62989893ffd6d88d2c219039ab472df4ada4cb7abceb88f9860`.
- Bytes read: 493058.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_pinned_baseline/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 088: data/reports/nextgen_pinned_baseline_review_20261030/s2_candidate_attempt01/candidate_baseline_parity.json
- Local path: [data/reports/nextgen_pinned_baseline_review_20261030/s2_candidate_attempt01/candidate_baseline_parity.json](data/reports/nextgen_pinned_baseline_review_20261030/s2_candidate_attempt01/candidate_baseline_parity.json).
- SHA-256 at document construction: `5849c72b39d1f2aeb2af8a10fea6bf2644b5927df17f851b09db2503a2b0cb43`.
- Bytes read: 815.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: ACTUAL_PINNED_BASELINE_MATCH_NO_OPTIMIZER.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 089: data/reports/nextgen_pinned_baseline_review_20261030/s2_candidate_attempt01/preparation_receipt.json
- Local path: [data/reports/nextgen_pinned_baseline_review_20261030/s2_candidate_attempt01/preparation_receipt.json](data/reports/nextgen_pinned_baseline_review_20261030/s2_candidate_attempt01/preparation_receipt.json).
- SHA-256 at document construction: `78c0087f4be8ad991677b0b10cabf0b65eb57939f315123f9b48f28dd3b57624`.
- Bytes read: 923.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: CANDIDATE_BASELINE_PREPARED_NOT_EXECUTED.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 090: data/reports/nextgen_pinned_baseline_review_20261030/s2_oracle_attempt01/oracle_request.json
- Local path: [data/reports/nextgen_pinned_baseline_review_20261030/s2_oracle_attempt01/oracle_request.json](data/reports/nextgen_pinned_baseline_review_20261030/s2_oracle_attempt01/oracle_request.json).
- SHA-256 at document construction: `99e8fd079c8b9424d618454960525950eb37bcb2ee66ee7046c743887b31c6ac`.
- Bytes read: 2720.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_prior_baseline_request/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 091: data/reports/nextgen_pinned_baseline_review_20261030/s2_oracle_attempt01/s2_replica_verification.json
- Local path: [data/reports/nextgen_pinned_baseline_review_20261030/s2_oracle_attempt01/s2_replica_verification.json](data/reports/nextgen_pinned_baseline_review_20261030/s2_oracle_attempt01/s2_replica_verification.json).
- SHA-256 at document construction: `6d609cca32335a9dc23c2f9d8b5475f61738ac83ad1e958f2b3f1e5ae79085d2`.
- Bytes read: 485.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: S2_COPY_VERIFIED_NOT_EVALUATED.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 092: data/reports/nextgen_pinned_baseline_review_20261030/s2_oracle_attempt03/oracle_request.json
- Local path: [data/reports/nextgen_pinned_baseline_review_20261030/s2_oracle_attempt03/oracle_request.json](data/reports/nextgen_pinned_baseline_review_20261030/s2_oracle_attempt03/oracle_request.json).
- SHA-256 at document construction: `99e8fd079c8b9424d618454960525950eb37bcb2ee66ee7046c743887b31c6ac`.
- Bytes read: 2720.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_prior_baseline_request/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 093: data/reports/nextgen_pinned_baseline_review_20261030/s2_oracle_attempt03/previous_baseline_oracle.json
- Local path: [data/reports/nextgen_pinned_baseline_review_20261030/s2_oracle_attempt03/previous_baseline_oracle.json](data/reports/nextgen_pinned_baseline_review_20261030/s2_oracle_attempt03/previous_baseline_oracle.json).
- SHA-256 at document construction: `0a822a68a84275b6b1c71c7e38c005c55c0e65a25b3d70c026450f296bb8591a`.
- Bytes read: 206247.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_previous_baseline_oracle/1.
- Declared status: MEASURED_PREVIOUS_BASELINE_ONLY_NOT_CANDIDATE_PARITY.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 094: data/reports/nextgen_pinned_baseline_review_20261030/s2_oracle_attempt03/previous_execution_ledger.json
- Local path: [data/reports/nextgen_pinned_baseline_review_20261030/s2_oracle_attempt03/previous_execution_ledger.json](data/reports/nextgen_pinned_baseline_review_20261030/s2_oracle_attempt03/previous_execution_ledger.json).
- SHA-256 at document construction: `2042c4558e106ed995a78b0d468f2c4a1bb3bf8b2c3750a4e91c9fa1a9eede6d`.
- Bytes read: 31172.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 095: data/reports/nextgen_pinned_baseline_review_20261030/s2_oracle_attempt03/s2_replica_verification.json
- Local path: [data/reports/nextgen_pinned_baseline_review_20261030/s2_oracle_attempt03/s2_replica_verification.json](data/reports/nextgen_pinned_baseline_review_20261030/s2_oracle_attempt03/s2_replica_verification.json).
- SHA-256 at document construction: `6d609cca32335a9dc23c2f9d8b5475f61738ac83ad1e958f2b3f1e5ae79085d2`.
- Bytes read: 485.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: S2_COPY_VERIFIED_NOT_EVALUATED.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 096: data/reports/nextgen_pinned_baseline_review_20261030/s6_actual_schema_receipt.json
- Local path: [data/reports/nextgen_pinned_baseline_review_20261030/s6_actual_schema_receipt.json](data/reports/nextgen_pinned_baseline_review_20261030/s6_actual_schema_receipt.json).
- SHA-256 at document construction: `a0194413d9257ad482f026352e0817106feda17bbf84592bd9896d6f27c50e7b`.
- Bytes read: 11203.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 097: data/reports/nextgen_pinned_baseline_review_20261030/s6_materialization_summary.json
- Local path: [data/reports/nextgen_pinned_baseline_review_20261030/s6_materialization_summary.json](data/reports/nextgen_pinned_baseline_review_20261030/s6_materialization_summary.json).
- SHA-256 at document construction: `a9f56351ba7834234dabc5e08a8a4d52be4d46b116499b20ef7e5599bf462057`.
- Bytes read: 571.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_prior_runtime_materialization/1.
- Declared status: HASH_VERIFIED_NO_ENGINE_EVALUATION.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 098: data/reports/nextgen_pinned_baseline_review_20261030/s6_oracle_preparation_receipt.json
- Local path: [data/reports/nextgen_pinned_baseline_review_20261030/s6_oracle_preparation_receipt.json](data/reports/nextgen_pinned_baseline_review_20261030/s6_oracle_preparation_receipt.json).
- SHA-256 at document construction: `2014731825cacb603472055ac2517ee49f39b547c8da922b1251fa61a442e175`.
- Bytes read: 500.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: PREPARED_NO_ENGINE_EVALUATION.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 099: data/reports/nextgen_pinned_baseline_review_20261030/test_receipt.json
- Local path: [data/reports/nextgen_pinned_baseline_review_20261030/test_receipt.json](data/reports/nextgen_pinned_baseline_review_20261030/test_receipt.json).
- SHA-256 at document construction: `c516968666e25f3509309289ef09c2b2de9df48a1c542d61bc9ee078f74e9a0c`.
- Bytes read: 378.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 100: data/reports/nextgen_stream_sort_full_proof_20261031/canonical_sort_receipt.json
- Local path: [data/reports/nextgen_stream_sort_full_proof_20261031/canonical_sort_receipt.json](data/reports/nextgen_stream_sort_full_proof_20261031/canonical_sort_receipt.json).
- SHA-256 at document construction: `56d1db9e8829e8e7ec7ccf8eb587412a5223a51c5eb38a2a2983037d033df054`.
- Bytes read: 527.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 101: data/reports/nextgen_stream_sort_full_proof_20261031/copy_receipt.json
- Local path: [data/reports/nextgen_stream_sort_full_proof_20261031/copy_receipt.json](data/reports/nextgen_stream_sort_full_proof_20261031/copy_receipt.json).
- SHA-256 at document construction: `b0bbcc51d278d74f03bebd9039854d9b0fe21348fd8c798e343d3e6e42502232`.
- Bytes read: 310.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 102: data/reports/nextgen_stream_sort_full_proof_20261031/full_equivalence_receipt.json
- Local path: [data/reports/nextgen_stream_sort_full_proof_20261031/full_equivalence_receipt.json](data/reports/nextgen_stream_sort_full_proof_20261031/full_equivalence_receipt.json).
- SHA-256 at document construction: `5b512f2a05010f18f718ac0c42bfa8f6d913d546b1b023f00b0a3881acc97a20`.
- Bytes read: 1559.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: FULL_812310317_BYTE_EQUALITY_PASS.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 103: data/reports/nextgen_stream_sort_full_proof_20261031/streamed_events.tsv.stream_sort_receipt.json
- Local path: [data/reports/nextgen_stream_sort_full_proof_20261031/streamed_events.tsv.stream_sort_receipt.json](data/reports/nextgen_stream_sort_full_proof_20261031/streamed_events.tsv.stream_sort_receipt.json).
- SHA-256 at document construction: `dfbf7b6eb10b98675082881daf452777b4e63ba0714b1f00545ec14cd288e045`.
- Bytes read: 797.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: lossless_stream_sort/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 104: data/reports/nextgen_protection_trace_20261031T015149Z/copy_receipt.json
- Local path: [data/reports/nextgen_protection_trace_20261031T015149Z/copy_receipt.json](data/reports/nextgen_protection_trace_20261031T015149Z/copy_receipt.json).
- SHA-256 at document construction: `8a31852ef527996ad6b3d10b9f51c592089889436cf824a131f5c0ad579fa139`.
- Bytes read: 744.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 105: data/reports/nextgen_protection_trace_20261031T015149Z/oracle_request.json
- Local path: [data/reports/nextgen_protection_trace_20261031T015149Z/oracle_request.json](data/reports/nextgen_protection_trace_20261031T015149Z/oracle_request.json).
- SHA-256 at document construction: `1acfad4372155a0331012c3337ed465a2b9fbdf688e491ad995ac872e9424edc`.
- Bytes read: 2776.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_prior_baseline_request/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 106: data/reports/nextgen_protection_trace_20261031T015149Z/previous_baseline_oracle.json
- Local path: [data/reports/nextgen_protection_trace_20261031T015149Z/previous_baseline_oracle.json](data/reports/nextgen_protection_trace_20261031T015149Z/previous_baseline_oracle.json).
- SHA-256 at document construction: `71c81622a93a56df5fe311955bd0e1ed4d9cad13c2ad3c3a8cfa85bd436e5cdc`.
- Bytes read: 206288.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_previous_baseline_oracle/1.
- Declared status: MEASURED_PREVIOUS_BASELINE_ONLY_NOT_CANDIDATE_PARITY.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 107: data/reports/nextgen_protection_trace_20261031T015149Z/previous_execution_ledger.json
- Local path: [data/reports/nextgen_protection_trace_20261031T015149Z/previous_execution_ledger.json](data/reports/nextgen_protection_trace_20261031T015149Z/previous_execution_ledger.json).
- SHA-256 at document construction: `2042c4558e106ed995a78b0d468f2c4a1bb3bf8b2c3750a4e91c9fa1a9eede6d`.
- Bytes read: 31172.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 108: data/reports/nextgen_protection_trace_20261031T015149Z/recovery_join.json
- Local path: [data/reports/nextgen_protection_trace_20261031T015149Z/recovery_join.json](data/reports/nextgen_protection_trace_20261031T015149Z/recovery_join.json).
- SHA-256 at document construction: `e32bef08a0e23a2e003cbab6b198578497b1589c2cb81122004682869df64482`.
- Bytes read: 2139.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: actual_recovery_predicate_ledger_join/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 109: data/reports/nextgen_protection_gate_trace_20261031T020032Z/copy_receipt.json
- Local path: [data/reports/nextgen_protection_gate_trace_20261031T020032Z/copy_receipt.json](data/reports/nextgen_protection_gate_trace_20261031T020032Z/copy_receipt.json).
- SHA-256 at document construction: `7367832dc992e81b9023e03b8d7cd6e7d1bd1a72747b6f466d12d749f495b4b7`.
- Bytes read: 744.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 110: data/reports/nextgen_protection_gate_trace_20261031T020032Z/oracle_request.json
- Local path: [data/reports/nextgen_protection_gate_trace_20261031T020032Z/oracle_request.json](data/reports/nextgen_protection_gate_trace_20261031T020032Z/oracle_request.json).
- SHA-256 at document construction: `d681ea80579a5086319295379bfc6aaee511cb2e725693f00bb25f5e08718481`.
- Bytes read: 2776.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_prior_baseline_request/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 111: data/reports/nextgen_protection_gate_trace_20261031T020032Z/previous_baseline_oracle.json
- Local path: [data/reports/nextgen_protection_gate_trace_20261031T020032Z/previous_baseline_oracle.json](data/reports/nextgen_protection_gate_trace_20261031T020032Z/previous_baseline_oracle.json).
- SHA-256 at document construction: `94347527d149c86f272c55b0fa2d6492e2458aa51ab9c67b3d2b39e7b6e80a8a`.
- Bytes read: 206290.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_previous_baseline_oracle/1.
- Declared status: MEASURED_PREVIOUS_BASELINE_ONLY_NOT_CANDIDATE_PARITY.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 112: data/reports/nextgen_protection_gate_trace_20261031T020032Z/previous_execution_ledger.json
- Local path: [data/reports/nextgen_protection_gate_trace_20261031T020032Z/previous_execution_ledger.json](data/reports/nextgen_protection_gate_trace_20261031T020032Z/previous_execution_ledger.json).
- SHA-256 at document construction: `2042c4558e106ed995a78b0d468f2c4a1bb3bf8b2c3750a4e91c9fa1a9eede6d`.
- Bytes read: 31172.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 113: data/reports/full_cell_campaign_20261031/runner_stage_01/inputs/catalog.json
- Local path: [data/reports/full_cell_campaign_20261031/runner_stage_01/inputs/catalog.json](data/reports/full_cell_campaign_20261031/runner_stage_01/inputs/catalog.json).
- SHA-256 at document construction: `612e842ef96b54eda4e065db61949b029777549f82b9efca517405aa1b52c1f4`.
- Bytes read: 1799104.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: factored_every_row_every_filter/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 114: data/reports/full_cell_campaign_20261031/runner_stage_01/inputs/catalog_manifest.json
- Local path: [data/reports/full_cell_campaign_20261031/runner_stage_01/inputs/catalog_manifest.json](data/reports/full_cell_campaign_20261031/runner_stage_01/inputs/catalog_manifest.json).
- SHA-256 at document construction: `504692268df141827a58af1594e20ee61d7d1e237980eeff8f51bcc31033c416`.
- Bytes read: 1944.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: every_row_every_filter_catalog/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 115: data/reports/full_cell_campaign_20261031/runner_stage_01/inputs/template_sources.json
- Local path: [data/reports/full_cell_campaign_20261031/runner_stage_01/inputs/template_sources.json](data/reports/full_cell_campaign_20261031/runner_stage_01/inputs/template_sources.json).
- SHA-256 at document construction: `3103517614877fd73f2d12284a33dc805ee21a0a53073412fecbd021e1011795`.
- Bytes read: 971.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 116: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/B_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/B_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/B_execution_ledger.json).
- SHA-256 at document construction: `ee4e96f58ec914681c15cf2fc6b0c0e1f896bb5ae983275ac3a30eb4303d7e91`.
- Bytes read: 51786.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 117: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/B_measurement.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/B_measurement.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/B_measurement.json).
- SHA-256 at document construction: `62d69561511bb53e54b3609867827204c403d3efd05f33c421a8821fb6d869d7`.
- Bytes read: 331006.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 118: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/X_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/X_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/X_execution_ledger.json).
- SHA-256 at document construction: `af2db6316a44e47cae5bd29b03b632f361d50c51dd6573929aca88bd9b4daba1`.
- Bytes read: 59916.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 119: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/X_measurement.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/X_measurement.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/X_measurement.json).
- SHA-256 at document construction: `8825eeb4c6db64ed03bb04816cd85f24534b080ee983e5af58ca00ed57638988`.
- Bytes read: 331003.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 120: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/Y_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/Y_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/Y_execution_ledger.json).
- SHA-256 at document construction: `ee4e96f58ec914681c15cf2fc6b0c0e1f896bb5ae983275ac3a30eb4303d7e91`.
- Bytes read: 51786.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 121: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/Y_measurement.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/Y_measurement.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/Y_measurement.json).
- SHA-256 at document construction: `3149af05847ed3774d90ebdb76794a85c8a4718fc36b67496de73508d3d96271`.
- Bytes read: 331006.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 122: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/Z_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/Z_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/Z_execution_ledger.json).
- SHA-256 at document construction: `af2db6316a44e47cae5bd29b03b632f361d50c51dd6573929aca88bd9b4daba1`.
- Bytes read: 59916.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 123: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/Z_measurement.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/Z_measurement.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/Z_measurement.json).
- SHA-256 at document construction: `513012010949248bc0167ca517320d4a8b007c996ba44ab71fd08f073c8a5cc9`.
- Bytes read: 331003.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 124: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/quartet.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/quartet.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SF_PROTECT_PG_OWN_YELLOW_15M/quartet.json).
- SHA-256 at document construction: `434a4f98a2d71c11325a7f23cf3efbf92f7dc7df1246b62fcbd22fe14fa62171`.
- Bytes read: 5008.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: prospective_quartet/1.
- Declared status: measured_operation_interaction.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 125: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/B_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/B_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/B_execution_ledger.json).
- SHA-256 at document construction: `ee4e96f58ec914681c15cf2fc6b0c0e1f896bb5ae983275ac3a30eb4303d7e91`.
- Bytes read: 51786.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 126: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/B_measurement.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/B_measurement.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/B_measurement.json).
- SHA-256 at document construction: `62d69561511bb53e54b3609867827204c403d3efd05f33c421a8821fb6d869d7`.
- Bytes read: 331006.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 127: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/X_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/X_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/X_execution_ledger.json).
- SHA-256 at document construction: `af2db6316a44e47cae5bd29b03b632f361d50c51dd6573929aca88bd9b4daba1`.
- Bytes read: 59916.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 128: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/X_measurement.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/X_measurement.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/X_measurement.json).
- SHA-256 at document construction: `8825eeb4c6db64ed03bb04816cd85f24534b080ee983e5af58ca00ed57638988`.
- Bytes read: 331003.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 129: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/Y_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/Y_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/Y_execution_ledger.json).
- SHA-256 at document construction: `ee4e96f58ec914681c15cf2fc6b0c0e1f896bb5ae983275ac3a30eb4303d7e91`.
- Bytes read: 51786.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 130: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/Y_measurement.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/Y_measurement.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/Y_measurement.json).
- SHA-256 at document construction: `d4db0e045dcb3f621dbf05229c7b361c8781ff3e28d3cfd401811b6fb6596d2f`.
- Bytes read: 331006.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 131: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/Z_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/Z_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/Z_execution_ledger.json).
- SHA-256 at document construction: `8473ae5d857fbc69b423b64f880dbf399278b10bd852f3f6100e2b26421c2638`.
- Bytes read: 59853.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 132: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/Z_measurement.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/Z_measurement.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/Z_measurement.json).
- SHA-256 at document construction: `0a36eef8ebe9c72aac679a07fc90fc975233c392d5251effabd0f5800dc942ac`.
- Bytes read: 331004.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 133: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/quartet.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/quartet.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_BUFFER_004/quartet.json).
- SHA-256 at document construction: `1e45d1f0b3af85253ee73879de5ceeb85a27fb393f6158418a6cb1cdf33befff`.
- Bytes read: 4912.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: prospective_quartet/1.
- Declared status: measured_operation_interaction.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 134: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/B_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/B_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/B_execution_ledger.json).
- SHA-256 at document construction: `ee4e96f58ec914681c15cf2fc6b0c0e1f896bb5ae983275ac3a30eb4303d7e91`.
- Bytes read: 51786.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 135: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/B_measurement.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/B_measurement.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/B_measurement.json).
- SHA-256 at document construction: `62d69561511bb53e54b3609867827204c403d3efd05f33c421a8821fb6d869d7`.
- Bytes read: 331006.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 136: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/X_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/X_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/X_execution_ledger.json).
- SHA-256 at document construction: `af2db6316a44e47cae5bd29b03b632f361d50c51dd6573929aca88bd9b4daba1`.
- Bytes read: 59916.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 137: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/X_measurement.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/X_measurement.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/X_measurement.json).
- SHA-256 at document construction: `8825eeb4c6db64ed03bb04816cd85f24534b080ee983e5af58ca00ed57638988`.
- Bytes read: 331003.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 138: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/Y_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/Y_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/Y_execution_ledger.json).
- SHA-256 at document construction: `ee4e96f58ec914681c15cf2fc6b0c0e1f896bb5ae983275ac3a30eb4303d7e91`.
- Bytes read: 51786.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 139: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/Y_measurement.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/Y_measurement.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/Y_measurement.json).
- SHA-256 at document construction: `77f447601aa3ec69a20c4ebfe9b45cd2d9892995fdd6f4dd1af10b7036473aef`.
- Bytes read: 331008.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 140: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/Z_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/Z_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/Z_execution_ledger.json).
- SHA-256 at document construction: `19baa778eceb6945aed4e03696420d2e69ea9259d8a89204cc3f78b7976d8ccd`.
- Bytes read: 68133.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 141: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/Z_measurement.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/Z_measurement.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/Z_measurement.json).
- SHA-256 at document construction: `e0741ede7e3413eef7caf5cbd412a824bad5358f69f70e6f69ca3542c8c3e664`.
- Bytes read: 331006.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 142: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/quartet.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/quartet.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/SS_PROTECT_PG_DROP_025/quartet.json).
- SHA-256 at document construction: `2248674155421f1b203b5efa15d4d0430b55043e367238adbe9ea073f91d845d`.
- Bytes read: 4910.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: prospective_quartet/1.
- Declared status: measured_operation_interaction.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 143: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/oracle_bootstrap_request.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/oracle_bootstrap_request.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/oracle_bootstrap_request.json).
- SHA-256 at document construction: `32d2b21be9805e862b86d653a3945d147bcd79513c99cb5a4374ee1f4ce01a1d`.
- Bytes read: 2741.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_prior_baseline_request/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 144: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/prepared_env.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/prepared_env.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/prepared_env.json).
- SHA-256 at document construction: `7b1450e708ec5305f2555009bc1c1d6222f7a264365b0c681d6a9cc48faaf5cc`.
- Bytes read: 4896.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 145: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/previous_baseline_oracle.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/previous_baseline_oracle.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/previous_baseline_oracle.json).
- SHA-256 at document construction: `db462c06bfc74c9010723144b206d828adabf90e0561d389e1e31ff320869d22`.
- Bytes read: 206270.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_previous_baseline_oracle/1.
- Declared status: MEASURED_PREVIOUS_BASELINE_ONLY_NOT_CANDIDATE_PARITY.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 146: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/previous_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/previous_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/previous_execution_ledger.json).
- SHA-256 at document construction: `2042c4558e106ed995a78b0d468f2c4a1bb3bf8b2c3750a4e91c9fa1a9eede6d`.
- Bytes read: 31172.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 147: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/previous_raw_capture_20261031T024335637495Z.provenance.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/previous_raw_capture_20261031T024335637495Z.provenance.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/previous_raw_capture_20261031T024335637495Z.provenance.json).
- SHA-256 at document construction: `96512509f550b214a112c7ec60dc038fd8d75c22c6baf521988a5922b3dc9a02`.
- Bytes read: 196873.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 148: data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/prospective_summary.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/prospective_summary.json](data/reports/v20_fullcorpus_extension_20261030/historical_protection_quartets_05/prospective_summary.json).
- SHA-256 at document construction: `95c742b1d4ddde5a262a59a2955942b4b2796122dadeaf7f0a0f85598d9e718c`.
- Bytes read: 9137.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: prospective_quartet_batch/1.
- Declared status: research_only_not_production.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 149: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/B_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/B_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/B_execution_ledger.json).
- SHA-256 at document construction: `795b9c206b8e865fa3e74781008c09f40445e096d36a0869482d8ba6b0b1ed0d`.
- Bytes read: 98470.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 150: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/B_measurement.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/B_measurement.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/B_measurement.json).
- SHA-256 at document construction: `4dcc3acf4959468a1b20c587c8f69c5a7125cae1f5f82ebed9ca6222f4558cb9`.
- Bytes read: 331007.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 151: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/X_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/X_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/X_execution_ledger.json).
- SHA-256 at document construction: `baa621d75f2f70b37a6e87877c758235be0ae0b66978c614be3ccdfa202ab40d`.
- Bytes read: 108829.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 152: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/X_measurement.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/X_measurement.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/X_measurement.json).
- SHA-256 at document construction: `3b295ddc67ed31fd649a53bbf911a2f29c9303e91f1abbca94d8fb678dc482ea`.
- Bytes read: 331006.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 153: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/Y_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/Y_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/Y_execution_ledger.json).
- SHA-256 at document construction: `795b9c206b8e865fa3e74781008c09f40445e096d36a0869482d8ba6b0b1ed0d`.
- Bytes read: 98470.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 154: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/Y_measurement.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/Y_measurement.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/Y_measurement.json).
- SHA-256 at document construction: `77214cb8f6ff94e3e084cdff58191d9d299558a843dbddd181a679b64df79ff5`.
- Bytes read: 331007.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 155: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/Z_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/Z_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/Z_execution_ledger.json).
- SHA-256 at document construction: `1b92697e1a6c4a83551c70b11b3035bbe510ada1bac1be721517c89b2b646fe2`.
- Bytes read: 108784.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 156: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/Z_measurement.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/Z_measurement.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/Z_measurement.json).
- SHA-256 at document construction: `26aee4e9331908867e107ee52b93848600bcbc56f86a9f10a2f007e7a248e692`.
- Bytes read: 331006.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 157: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/quartet.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/quartet.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SF_PROTECT_PG_OWN_YELLOW_15M/quartet.json).
- SHA-256 at document construction: `e50c44fe78c145e69e035fe14b4ca3c1863f6e83ca2590193e2e758a910c01d3`.
- Bytes read: 5129.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: prospective_quartet/1.
- Declared status: measured_operation_interaction.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 158: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/B_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/B_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/B_execution_ledger.json).
- SHA-256 at document construction: `795b9c206b8e865fa3e74781008c09f40445e096d36a0869482d8ba6b0b1ed0d`.
- Bytes read: 98470.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 159: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/B_measurement.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/B_measurement.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/B_measurement.json).
- SHA-256 at document construction: `4dcc3acf4959468a1b20c587c8f69c5a7125cae1f5f82ebed9ca6222f4558cb9`.
- Bytes read: 331007.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 160: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/X_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/X_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/X_execution_ledger.json).
- SHA-256 at document construction: `baa621d75f2f70b37a6e87877c758235be0ae0b66978c614be3ccdfa202ab40d`.
- Bytes read: 108829.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 161: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/X_measurement.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/X_measurement.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/X_measurement.json).
- SHA-256 at document construction: `3b295ddc67ed31fd649a53bbf911a2f29c9303e91f1abbca94d8fb678dc482ea`.
- Bytes read: 331006.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 162: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/Y_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/Y_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/Y_execution_ledger.json).
- SHA-256 at document construction: `d500d7c850181de15eae2a4bad2533daf54bbc6228a3b14a9a463cb1dffb8477`.
- Bytes read: 107112.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 163: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/Y_measurement.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/Y_measurement.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/Y_measurement.json).
- SHA-256 at document construction: `d61a2b8e6705cfc6a27f195f5b60fa6339cee70376736918c8e48524771f296f`.
- Bytes read: 331008.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 164: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/Z_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/Z_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/Z_execution_ledger.json).
- SHA-256 at document construction: `b68928fe4547c5cdc1032ab720db98c399040a32d983341343a4efe4eb9b1fee`.
- Bytes read: 117451.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 165: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/Z_measurement.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/Z_measurement.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/Z_measurement.json).
- SHA-256 at document construction: `025836be47e6edecb64ec018fdf5940c97c4f2c00f500dcc0b0ae535354a5675`.
- Bytes read: 331008.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 166: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/quartet.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/quartet.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_BUFFER_004/quartet.json).
- SHA-256 at document construction: `c0f3d2c50b620a06881dadc53f583bd777053ca84412bf55fd8715ae3c1e7019`.
- Bytes read: 5001.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: prospective_quartet/1.
- Declared status: measured_operation_interaction.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 167: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/B_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/B_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/B_execution_ledger.json).
- SHA-256 at document construction: `795b9c206b8e865fa3e74781008c09f40445e096d36a0869482d8ba6b0b1ed0d`.
- Bytes read: 98470.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 168: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/B_measurement.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/B_measurement.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/B_measurement.json).
- SHA-256 at document construction: `4dcc3acf4959468a1b20c587c8f69c5a7125cae1f5f82ebed9ca6222f4558cb9`.
- Bytes read: 331007.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 169: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/X_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/X_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/X_execution_ledger.json).
- SHA-256 at document construction: `baa621d75f2f70b37a6e87877c758235be0ae0b66978c614be3ccdfa202ab40d`.
- Bytes read: 108829.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 170: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/X_measurement.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/X_measurement.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/X_measurement.json).
- SHA-256 at document construction: `3b295ddc67ed31fd649a53bbf911a2f29c9303e91f1abbca94d8fb678dc482ea`.
- Bytes read: 331006.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 171: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/Y_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/Y_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/Y_execution_ledger.json).
- SHA-256 at document construction: `795b9c206b8e865fa3e74781008c09f40445e096d36a0869482d8ba6b0b1ed0d`.
- Bytes read: 98470.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 172: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/Y_measurement.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/Y_measurement.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/Y_measurement.json).
- SHA-256 at document construction: `891beb7c94e7c873ca539943076e93aa6a394e3cdbe66a9b40653df11a37d83e`.
- Bytes read: 331009.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 173: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/Z_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/Z_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/Z_execution_ledger.json).
- SHA-256 at document construction: `b0bbbb9828c9e9bf593c6effbfd444671b33959ed0a48b6577dfe80e6b29b29c`.
- Bytes read: 112668.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 174: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/Z_measurement.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/Z_measurement.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/Z_measurement.json).
- SHA-256 at document construction: `676824517c22942cb038dad88d0614274a875ac1e32fe7d5d114ea03888a60c1`.
- Bytes read: 331009.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 175: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/quartet.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/quartet.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/SS_PROTECT_PG_DROP_025/quartet.json).
- SHA-256 at document construction: `99ba012797151466b45e837149f5f89793d0856ed31b4eda44bae82b4e6955ea`.
- Bytes read: 4983.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: prospective_quartet/1.
- Declared status: measured_operation_interaction.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 176: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/corrected_authority_bootstrap.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/corrected_authority_bootstrap.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/corrected_authority_bootstrap.json).
- SHA-256 at document construction: `ee854603c9d9df31e579da2fd3888d5fe907feb49545b66825ef5a1101928b12`.
- Bytes read: 912.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: corrected_authority_quartet_bootstrap/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 177: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/oracle_bootstrap_request.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/oracle_bootstrap_request.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/oracle_bootstrap_request.json).
- SHA-256 at document construction: `76c6b4928734a354d57af3817fc1d36793b5c9e3fd6c33ee9b4e1d1e8e701c8f`.
- Bytes read: 3100.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_prior_baseline_request/1.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 178: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/prepared_env.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/prepared_env.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/prepared_env.json).
- SHA-256 at document construction: `f5470b7befa64fd3a6baa08cf4d5ae19711d2f8c868ca5ae5395839b002b79fd`.
- Bytes read: 5057.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 179: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/previous_baseline_oracle.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/previous_baseline_oracle.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/previous_baseline_oracle.json).
- SHA-256 at document construction: `91358c0a196217096787acdf0888dbd15ccd77445e8786135c73832a0af7f4de`.
- Bytes read: 206278.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: v15_previous_baseline_oracle/1.
- Declared status: MEASURED_PREVIOUS_BASELINE_ONLY_NOT_CANDIDATE_PARITY.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
### Evidence 180: data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/previous_execution_ledger.json
- Local path: [data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/previous_execution_ledger.json](data/reports/v20_fullcorpus_extension_20261030/corrected_protection_quartets_06/previous_execution_ledger.json).
- SHA-256 at document construction: `f946a30a675fb48d49471bec061952bf42311cd119b48aeaf0439d254bd66ffe`.
- Bytes read: 59024.
- Stable size/mtime across this read: True.
- Artifact kind: JSON receipt, manifest, or structured evidence.
- Declared schema: not declared at the top level.
- Declared status: inspect the actual record; no top-level status copied.
- Primary use: inspect this artifact for the exact source, test, runtime, or evidence boundary it records.
- Review boundary: existence and a matching file hash do not prove the represented trading claim.
- Next action: verify this file against the current campaign or model consumer before relying on it.
- Source entry points: this index does not infer callable behavior from a receipt.
- Preservation: keep the original record and its computation dates when adding a later result.
## 17. Final handoff checklist for the next orchestrator
- Read the original user request and the latest exhaustive-cell correction before doing anything else.
- Keep v20 redesign and actual evidence application as the primary objective.
- Keep or spawn cheap agents and give them execution ownership.
- Do not interrupt the agents because this document exists.
- Inspect actual current process states before restarting anything.
- Inspect whether the full-cell runner has now launched on all four hosts.
- Inspect completed cell counts rather than relying on earlier readiness reports.
- Read the latest compact/factored catalog manifest and its exact total.
- Confirm that the campaign has no yellow-only or low-pos exclusion.
- Confirm that fresh and historical observations share explicit compatible identity rules.
- Confirm that every promotion/demotion has at least 101 proven results.
- Keep unknown, failed, duplicate, not-applied, and deferred statuses explicit.
- Preserve original baseline records and label corrected-code experiments separately.
- Use the 5T archive as a source for actual ingestion, not a reason to create another preservation project.
- Connect all emitted evidence to actual training and future test selection.
- Check the S2 gate before claiming any new NN is promoted.
- Check the actual winning NN/input comparison before claiming 90% compute reduction.
- Check actual 60D output before claiming weekend testing is enabled.
- Check daily all-side catch_up completion on S1/S2 before retiring temporary workers.
- If the controller still shows paused, distinguish that UI state from the later explicit instruction to continue work.
- The available goal tools previously could not resume the controller; the user was told automatic continuation requires its Resume control.
- Do not mark the goal complete or blocked merely because the task is large or a deadline is difficult.
- At a genuine impasse, report the exact missing dependency and continue independent work where possible.
- At each handoff, update this evidence map with measured progress rather than retelling the same plan.
- Never forget the user’s instruction: spawn cheap agents, manage them, and do not lose focus on the v20 neural-network redesign.
## 18. Construction scope and document validation
This document was generated locally while agents continued their assigned work.
The evidence index is a snapshot; it is intentionally not a claim that active artifacts will never change.
The category/tab matrix specifies work to perform and is not fabricated measured coverage.
The source-family packets specify ingestion obligations and are not fabricated trainer receipts.
The current campaign deadline was preserved rather than reset to the handoff time.
The document includes the initial request and subsequent user corrections verbatim.
No private keys or credentials are embedded.
No financial performance number has been invented to fill the document.
The source manifest companion is data/reports/v20_handoff_build_20261031/evidence_manifest.json.
Construction timestamp UTC: 2026-10-31T04:47:08.855766+00:00.

## 19. Late operational updates while agents continued working

These updates were received during document construction.
They do not erase earlier failed attempts or change their computation dates.

### Explicit coverage-only resource approval

- Root approved a new isolated diagnostic coverage contract.
- It is not a change to the official version-gate resource contract.
- Local disk reserve: 2 GiB per host.
- New output cap: 512 MiB per host.
- Prelaunch available RAM: at least 3 GiB.
- The total pool must preserve 3 GiB available RAM for other required work.
- Per-process address-space cap: 8 GiB.
- BLAS and OMP thread counts: one per worker process.
- Initial workers: S1 two, S2 four, S5 two, S6 four.
- Initial batches: at most 200 cells before checkpoint/reassessment.
- Ramp based on measured actual resource use and completed-cell throughput.
- Use cooperative stop/checkpoint behavior for the owned campaign workers.
- Never spoof disk usage or memory readings.
- Never silently lower the official 7 GiB RAM / 20 GiB disk gate prerequisites.
- The outer wrapper is tools/v15_nextgen_coverage_launch.py.
- Its reported current source hash was b3f6b2343b66340bb71294213e939ee3323bc3bc9cb8a842010f7a4ff89bac30.
- Linux-specific wrapper checks were still running when this update was recorded.
- A Mac address-space-limit probe failed because that preexec limit was unsupported there.
- That Mac failure was preserved; Linux test success must be checked directly.
- The wrapper requests a cooperative stop before exhausting the output cap.
- A completed wrapper test is not proof that the engine campaign launched.

### Actual admission controller

- The current active admission controller found by the compute agent was tools/fleet_dispatch.py.
- Its native expiring manual hold file is data/fleet_hold_hosts.json.
- Existing entries already held S2/S5/S6 for FIN 7D work at the inspected checkpoint.
- Preserve those pre-existing entries and their ownership reasons.
- Do not overwrite the account-specific preopen pause mechanism.
- S1 was not held at that checkpoint.
- An owned S1 entry with native expiry may be used only if needed for campaign capacity.
- The older fleet_drain mechanism was not the primary active launcher path.
- This discovery supersedes the earlier idea of inventing a new expiry guardian around fleet_drain.
- Do not restart or terminate ordinary jobs merely because an old control-file description differs.

### Portable campaign contexts

- All four portable contexts were reported ready at 2026-10-31T04:41:23Z.
- Common request path: /home/niels/v15_bench/reviews/authority_arms_20261031_review1/fullcell_context_01/oracle_request.json.
- The same directory contains corrected_oracle.json for the source-bound crypto-short control.
- Each host has its own inode/materialization verification and copied accepted/default/policy/routing/bootstrap inputs.
- The private output side is fullcell_context_01_side.
- No missing dependency is allowed to fall back into a live production directory.
- The current common runtime is the measured corrected 494/211 era.
- The separate measured b287/211 era adds two actual False-default fields.
- Changing campaign era must be explicit and must retain the corresponding baseline control.
- Do not count an ignored new field in the older era as a proven zero.

### Full-cell catalog materialization correction

- The first expanded Cartesian gzip failed on Mac temporary disk capacity.
- Its incomplete size was reported as 328,967,187 bytes.
- Only that own incomplete generated artifact was removed.
- The four source template books remained intact.
- The replacement is a lossless factored row/header catalog with deterministic lazy cell IDs.
- A factored catalog must still expose the exact full Cartesian target count.
- Do not describe the removed partial as a completed catalog.
- Do not convert this staging incident into a claim that 5T archival space is unavailable.

### Existing evidence recovery: latest concrete observations

- A local historical ADBE_SHORT progress file was read with a stable before/after source hash.
- It contained 2,826 rows and 18,299 numeric yellow observations.
- These raw observations lacked the per-row provenance needed for the strict current threshold proof.
- That file's source hash was 4fbcb46422b7a263595face1eccf943fab830caa636e8bab61d5591cb7855e2c.
- The detailed receipt is data/reports/per_cell_evidence_probe_20261031T044439Z.json.
- A bounded S5 CMC_SHORT file exposed 72 rows and some candidate cells surviving basic exclusions.
- Its source hash was a89b3b75d8209757a2983df3128bb9fb4c12a6da147beca51906f346bc0aa93a.
- Missing source/version joins still prevent those candidate observations from being credited blindly.
- The detailed receipt is data/reports/per_cell_s5_probe_20261031T0445Z.json.
- S1's CMC_SHORT file was byte-identical to the S5 copy.
- The two copies are replicas, not two independent trials.
- S2's expected path was absent in the bounded check.
- S6's bounded query returned no result; its status remains unknown, not absent.
- The cross-host receipt is data/reports/per_cell_progress_host_dedup_20261031T0446Z.json.
- Continue recovering joins and applying useful historical context; do not discard the data merely because one proof field is missing.
- Keep proven-threshold eligibility separate from broader historical learning usefulness.

### Existing scripts: reuse carefully

- tools/v15_filter_discovery.py Stage F already contains a real full-grid evaluation path.
- It uses the actual prepared evaluator and fork-shared NPZ data.
- Its legacy known-field, unwired, and conflict exclusions must remain explicit under the latest scope.
- Its baseline ingestion and provenance still need verification for the current campaign.
- tools/v15_full_sweep_driver.py is not a safe shortcut for the isolated campaign without review.
- That legacy driver can reset disabled sides and write ordinary production-oriented outputs.
- tools/row_by_row_filter_sweep.py is an old capped demonstration.
- It limits rows/filters and sums separate filter gains in a way that is not the required real joint evaluation.
- Do not use that demonstration as a source of valid full-cell results.
- Prefer the reviewed prepared-engine campaign runner and its exact per-cell receipts.

### How to resume after reading this document

- Check actual agent states immediately.
- Keep the running agents working; do not stop them for a fresh planning cycle.
- Read the finalized runner-stage receipt and current campaign status.
- If workers are already live, poll those exact handles or processes before doing anything else.
- If only staging is complete, launch the first verified S6 batch and then all four hosts.
- Count actual completed cells, not queued work.
- Track the original 05:28 and 06:28 UTC deadlines without moving them.
- Keep the neural-network consumer and full archived-data application in the same working plan.
- Do not spend another hour on storage, documentation, or optional tooling while coverage is idle.
