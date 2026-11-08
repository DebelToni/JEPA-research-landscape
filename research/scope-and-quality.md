# JEPA landscape: search scope and evidence quality

**Cutoff: 7 November 2026 UTC.** This is a discovery and appraisal protocol, not a synthesis of the papers. Anchor identifiers and titles below were checked against their primary arXiv abstract pages on that date. Recent entries are examples found in this search, not a claim of completeness or of state-of-the-art performance. Abstract-page verification establishes identity and reported scope only; it does not verify experiments or every version-specific claim. Recheck version histories and primary full text when extracting evidence.

## Scientific boundary

The central question is how learned representations or simulators predict aspects of a system beyond the observations currently available, and how those predictions support understanding, control, planning or scientific inference. The landscape includes JEPA by name and adjacent approaches with a concrete predictive or dynamical connection:

- **Predictive self-supervision:** context-to-target representation prediction, including masked, temporal and multimodal targets; also collapse prevention as a mechanism-level comparison.
- **Action-conditioned dynamics and planning:** latent or observation-space transitions, model-based RL, model predictive control, imagined rollouts and goal-conditioned planning. Record whether actions actually condition the prediction and whether predictions actually affect decisions.
- **VLA and robotics:** action policies only when they explicitly learn, use, evaluate or meaningfully ablate predictive state, environment dynamics, future observations or model-based planning. General VLA is boundary context, not automatic inclusion.
- **Generative video and interactive environments:** future-frame or world generation with temporal, interactive, counterfactual or control relevance. Generic text-to-video quality alone is insufficient.
- **Physical systems:** learned forward/inverse dynamics, system identification, neural operators and physics-informed simulators where the connection to prediction, intervention, rollouts, control or scientific inference is explicit. A static PDE solver benchmark without a meaningful dynamics or inference connection is boundary context.

Shared vocabulary is neither necessary nor sufficient. A feed-forward policy, a generic foundation model or an image generator does not become a world model merely by its title. Conversely, a model can be relevant without saying JEPA or using an EMA teacher. Include historically important ancestors when they explain a mechanism or evaluation used by a primary paper, but label their relationship rather than implying direct ancestry.

## Recall-first discovery, selective inclusion

1. **Collect candidates broadly.** Query arXiv, Semantic Scholar/OpenAlex or comparable scholarly indexes, conference proceedings and primary lab/project pages. Search titles, abstracts, full text where indexed, terminology variants, author-provided keywords and cited methods. Record query, source, retrieval time, date window and raw results or stable result links. Search without a date restriction first, then use overlapping recent windows; do not use a fixed citation threshold.
2. **Normalize and deduplicate.** Resolve arXiv ID, DOI, venue version and versions to a single work where justified; preserve all identifiers and accessed version. Distinguish a new paper from a substantially revised version or renamed title. If identity is ambiguous, retain separate records pending inspection.
3. **Screen in two stages.** First screen title/abstract liberally, retaining uncertain candidates. Then inspect full paper, supplementary material and available code for the actual prediction target, conditioning, objective and use. Assign a tier below with a short, quoted or pinpointed rationale. Retain exclusion reasons and periodically inspect a sample of excluded items to catch false negatives.
4. **Expand the graph iteratively.** For each accepted seed, inspect references and forward citations, with multiple indexes where available. Expand relevant neighbors by mechanism, evaluation or historical lineage, not merely any co-citation. Capture the citing/cited work, edge direction, source, retrieval date and why the edge matters. Forward-citation databases are incomplete and time-lagged; repeat recent-window and keyword searches independently. No citation count gate. Stop when a documented pass yields few new relevant works, reporting residual uncertainty rather than asserting completeness.
5. **Guard corpus composition.** Record branch and relevance separately; keep generic VLA, video and PDE results in a screened boundary pool. Review samples per branch and per query rather than allowing prolific branches to dominate by raw retrieval count. Track unique candidates, inclusion/exclusion/uncertain counts and yield per query and graph hop; do not impose a fixed quota that suppresses genuine relevant work.

### Relevance tiers

- **T1, direct:** JEPA or closely related joint-embedding predictive objective, or a paper whose main contribution directly studies predictive latent representations and their learning or use.
- **T2, substantive adjacent:** a primary contribution to learned dynamical prediction, action-conditioned planning, predictive VLA/video world models, or physical system dynamics/inference with a concrete connection to the central question. The model need not use a joint-embedding loss.
- **T3, boundary/mechanism:** general VLA policies, self-supervised regularizers, static operators, or generation systems that supply a necessary baseline, component or clearly articulated comparison, but lack the direct predictive connection. Retain selectively with the connection recorded.
- **Exclude:** no substantive predictive, dynamical, planning, or justified mechanism relationship after full-text screening. **Uncertain** is a temporary status, not an exclusion or a tier.

Tiers indicate relevance, not quality. One paper can have multiple branch labels and exactly one best-supported relevance tier. Preliminary seed labels below are starting hypotheses, not full-text adjudications.

### Reproducible query bank

Run each phrase and its synonyms separately as well as in combinations; search engines differ in Boolean and quotation support. Restricting all queries to `JEPA` would miss entire branches.

- `"joint embedding" predictive architecture`; `"masked prediction" "latent representations"`; `"self-supervised" "future representation" video`; `"predictive coding" representation dynamics`; `data2vec teacher student target representation`; `"variance invariance covariance" collapse`.
- `"latent dynamics" "action conditioned" planning`; `"world model" "model predictive control"`; `"model based reinforcement learning" imagined rollout`; `"visual planning" goal conditioned learned dynamics`; `"object centric" dynamics counterfactual`.
- `"vision language action" "world model"`; `robot policy "future state" latent prediction`; `robot manipulation predictive representation action conditioned`; `"VLA" imagined rollout planning`. Separately search generic VLA for boundary comparators, then apply the inclusion rule.
- `"interactive" "generative" "world model" video`; `"action conditioned" video prediction environment`; `"video diffusion" robot dynamics planning`; `"controllable" video simulator physical consistency`.
- `"neural operator" time dependent PDE extrapolation`; `"physics informed" operator dynamics`; `"system identification" nonlinear dynamics sparse`; `"foundation model" dynamical systems inference`; `"learned simulator" conservation control`.
- For every seed: exact title, author + distinctive method name, `cites:<identifier>` where supported, and its reference list. Search recent papers using both `world action model` and `world model action policy`, without assuming these names imply equivalent methods.

## Anchor proposal: 22 works

These seed breadth, not a ranked reading list. All links below are primary paper pages; T3 entries are deliberately chosen controls for relevance boundaries.

| Branch | Anchor and identifier | Initial tier / reason to inspect |
|---|---|---|
| Predictive SSL | [I-JEPA, 2301.08243](https://arxiv.org/abs/2301.08243) | T1: named image JEPA anchor |
| Predictive SSL | [V-JEPA 2, 2506.09985](https://arxiv.org/abs/2506.09985) | T1: video prediction and planning |
| Predictive SSL | [V-JEPA 2.1, 2603.14482](https://arxiv.org/abs/2603.14482) | T1: recent video self-supervised representation work |
| Predictive SSL | [data2vec, 2202.03555](https://arxiv.org/abs/2202.03555) | T1: cross-modal self-supervised target representations |
| Predictive SSL | [VICReg, 2105.04906](https://arxiv.org/abs/2105.04906) | T3: regularization and collapse comparison, not automatically a dynamics model |
| Planning | [Mastering Diverse Domains through World Models, 2301.04104](https://arxiv.org/abs/2301.04104) | T2: Dreamer world-model control |
| Planning | [TD-MPC2, 2310.16828](https://arxiv.org/abs/2310.16828) | T2: world models and continuous control |
| Planning | [H-JEPA, 2610.06805](https://arxiv.org/abs/2610.06805) | T1: recent hierarchical visual-planning JEPA |
| Robotics | [SLIM-0.5B, 2608.09771](https://arxiv.org/abs/2608.09771) | T2: action-grounded predictive latents in manipulation |
| VLA | [π₀, 2410.24164](https://arxiv.org/abs/2410.24164) | T3: general robot VLA policy boundary comparator |
| VLA | [WorldVLA, 2506.21539](https://arxiv.org/abs/2506.21539) | T2: action world-model connection to inspect |
| VLA | [RynnVLA-002, 2511.17502](https://arxiv.org/abs/2511.17502) | T2: unified VLA and world-model claim |
| VLA | [Token-World, 2610.00575](https://arxiv.org/abs/2610.00575) | T2: world modeling in VLM token space |
| Robotics | [LeWAM, 2610.12818](https://arxiv.org/abs/2610.12818) | T1: recent JEPA world/action modeling claim |
| Video | [Genie, 2402.15391](https://arxiv.org/abs/2402.15391) | T2: generative interactive environments |
| Video | [Cosmos World Foundation Model Platform for Physical AI, 2501.03575](https://arxiv.org/abs/2501.03575) | T2: physical-AI world-generation platform |
| Video | [World Simulation with Video Foundation Models for Physical AI, 2511.00062](https://arxiv.org/abs/2511.00062) | T2: simulation-oriented video foundation models |
| Physics | [Fourier Neural Operator, 2010.08895](https://arxiv.org/abs/2010.08895) | T3: operator-learning mechanism anchor; screen applications individually |
| Physics | [Physics-Informed Neural Operator, 2111.03794](https://arxiv.org/abs/2111.03794) | T3: physics-informed mechanism anchor; do not assume control evaluation |
| Physics | [Towards Universal Neural Operators through Multiphysics Pretraining, 2511.10829](https://arxiv.org/abs/2511.10829) | T2/T3 pending full text: multiphysics transfer and dynamics relevance |
| Identification | [Discovering governing equations from data, 1509.03580](https://arxiv.org/abs/1509.03580) | T2: sparse nonlinear system identification |
| Identification | [Zero-shot Imputation with Foundation Inference Models for Dynamical Systems, 2402.07594](https://arxiv.org/abs/2402.07594) | T2: dynamical-system inference; verify paper version and evaluation |

## Evidence and quality: separate axes

For each substantive claim, record **claim, precise location, experimental setting, comparator, metric, uncertainty and qualifying conditions**. Link exact paper version, section/table/figure, appendix and, where applicable, repository commit, dataset version and evaluation script. Abstracts are useful for discovery but insufficient to establish detailed results. A missing detail is `not reported` after checking relevant sources, not an inferred failure.

1. **Extraction confidence:** confidence that the record faithfully represents the source: high for unambiguous, directly located text/table; medium for indirect or incompletely specified evidence; low for ambiguous parsing, second-hand evidence or unresolved contradictions. Include supporting quote or location and note reviewer interpretation. This assesses the extraction, not whether the experiment proves the claim.
2. **Evidence strength for the specific claim:** assess design and execution: appropriate controls/baselines and comparable compute/data; multiple seeds or meaningful uncertainty; held-out and shift evaluation; isolation of the claimed component; adequate sample size and disclosed failure modes; whether the measurement actually tests the claim; independent replication where available. A single clean experiment can support a narrow claim without establishing generality. Label strength qualitatively with reasons, and do not treat a venue, model scale or citation count as proof.
3. **Relevance:** T1–T3 above, assigned independently. A directly relevant paper can have weak evidence; a strong paper can be peripheral. Do not combine these axes into an opaque single score.

### Dimensions to extract when applicable

- **Mechanism and supervision:** inputs, target, temporal horizon, objective, stop-gradient/teacher, anti-collapse mechanism, latent versus generated state, action and language conditioning, training data and labels, inference-time information. Distinguish learned dynamics from policy outputs and from retrospective prediction.
- **Predictive and physical validity:** one-step versus closed-loop/rollout error, horizon degradation, uncertainty/calibration, stochasticity and multimodality, conservation or constraint violations, intervention/counterfactual tests, discretization and boundary conditions where applicable. Image realism alone does not establish physical or interventional accuracy.
- **Decision utility:** actual planning or policy use, goals, action space, execution feedback, task success and baselines; distinguish offline predicted reward from real or simulated closed-loop outcomes. Identify access to ground-truth state, demonstrations, resets or privileged information.
- **Generalization and leakage:** splits at trajectory, scene, object, task, embodiment or physical regime as appropriate; whether pretraining exposure and benchmark contamination can be ruled out; extrapolation versus interpolation. Unknown overlap remains unknown.
- **Fairness and robustness:** training and inference compute, data volume, tuning budget, latency and memory where reported; matched baselines, sensitivity to seeds, ablations, noise, distribution shift and domain-specific safety. Treat unreported costs as missing, not zero.
- **Reproducibility and accessibility:** code, weights, dataset, simulator and preprocessing availability, versions, hyperparameters, licensing, hardware, independent execution and unresolved implementation dependencies. Availability is not proof that results reproduce.
- **Scope of evidence:** synthetic versus real-world, scale and diversity, benchmark construction and potential shortcut learning. Scientific operator results should state PDE family, initial/boundary conditions, numerical reference and resolution.

The [NeurIPS Paper Checklist](https://neurips.cc/public/guides/PaperChecklist) provides primary guidance on claims, limitations, experimental details, statistical significance, compute and data documentation. [Investigating Compounding Prediction Errors in Learned Dynamics Models, 2203.09637](https://arxiv.org/abs/2203.09637) motivates specifically examining multi-step prediction errors rather than assuming one-step accuracy implies reliable long-horizon use. These sources motivate dimensions, not a universal numeric quality rubric.

### Negative results and limitations

Record valid negative or null results when a well-defined comparison fails to show improvement, an ablation finds no measurable effect, a model fails under shift, or an approach has a documented domain where it is inapplicable. Capture metric, uncertainty, power/sample size when available, baseline and conditions. Absence of statistical significance alone does not demonstrate equivalence; missing experiments are missing evidence, not negative results. Publication and search bias can hide unpublished failures.

Separate **author-stated limitations** with a direct citation from **reviewer-inferred limitations**, which must include the observed basis and be marked as inference. For example, lack of a reported real-robot test supports “real-robot performance not established in the inspected sources,” not “the method fails on robots.” Preserve disagreements between paper, supplement and code rather than silently resolving them.

## Limits of this proposal

This is a scoped discovery starting point, not an exhaustive census or independent verification of experimental claims. Source search and anchor title checks were performed at the stated cutoff; no citation-graph expansion, comprehensive full-text review, benchmark audit or cross-paper scientific synthesis has been completed here. Recent preprints and search indexes can change; pin the actual version examined in subsequent work.
