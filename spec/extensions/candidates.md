# Candidate extensions (informative)

Status: working list · Date: 2026-11-06 · Not normative: an extension is specified only when it is registered (`README.md`).

This list surveys the extensions the claimset format may need, by kind of work, so that the core can be checked against them before the platform goes live, and extensions can be written in order of need. Each entry says what the extension would add, on which objects, which community standard it would follow, and where the work happens. Names are of kinds of work, not disciplines: a paper uses every extension its work needs.

## 1. What stays in the core

An extension adds what one kind of work needs. What several kinds of work need, or what claims are compared by across fields, belongs in the core (§2.7). The survey below found these needs shared by many entries; they are proposed for the core, not for an extension:

| Need | Shared by | Proposal for the core |
|---|---|---|
| Physical and biological things as terms: a compound or material, an organism or strain, a gene, protein or cell line, an instrument or device, a physical object or specimen (a fossil, a manuscript, a sample) | synthesis, wet-lab, omics, ecology, archaeology, archival, engineering | New definition kinds (§3.2), so that claims about them can be found and compared across fields; their registry identifiers are already in `identifiers` |
| Evidence that is direct observation or characterisation rather than statistical or qualitative analysis: a spectrum, a crystal structure, a band on a gel, a detection, a described specimen or document | synthesis, crystallography, wet-lab, astronomy, taxonomy, archival, archaeology | A support kind and profile of its own (§3.4, §4); today these are forced into `empirical_quant` or `empirical_qual` |
| Limits and censored values: an upper limit with no point value, a value below a detection limit | particle physics, environmental sampling, analytical chemistry, clinical laboratory results, astronomy | A typed form of `value` (a bound with its level, or below/above a stated limit), so that machines can compare it |
| Several components of uncertainty, and what a ± means (SD, SE, interval) | particle physics, metrology, every quantitative field | Typed uncertainty on result entries, besides `interval` |
| A registration outside the lab record (ClinicalTrials.gov, OSF, PROSPERO, AsPredicted) as the anchor of a plan | trial, behavioural experiment, systematic review, survey, replication | A `registry` value of `planned_at_anchor` (and checking the field's values, which the validator does not do today) |
| The link from a measure to the construct it measures | behavioural experiment, survey, psychometrics, education, health | A `measures` link on `variable` definitions, naming `concept` definitions |
| Where the cases are: coordinates or an area, in a stated reference system | ecology, earth observation, archaeology, epidemiology, astronomy (celestial), geography | A typed location in `scope` and on definitions of kind `setting` |
| Groups that publish as one (a collaboration, a consortium) | particle physics, astronomy, genomics consortia, multi-site trials | Organisations allowed in the position `contributor` |

The field trials (one real paper per kind of work) decide which of these enter the core before launch, and in what form.

## 2. What is already in the format

The format was reviewed for parts that belong to one kind of work and should move to an extension.

- **Moved in v0.38:** the excerpt's `gloss` (Leipzig glossing), now the `linguistics` extension.
- **Kept in the core**, because several kinds of work use them:
  - excerpts, with their structured locators into captured sources and their `translation`: qualitative research, law, history, linguistics and journalism studies quote words from data;
  - references held as captured copies (warning 32): any work that cites web pages;
  - the test flag that came with qualitative work in the lab record, `sources_captured_after_plan`: any test run on captured sources can have it. The flags about coding (`coded_before_plan`, `codebook_changed_after_plan`) moved to the `qualitative` extension in v0.38;
  - the `qual` profile's closed lists (`data_sources`, `analytic_approach`): like the `quant` profile's `design`, they classify the method coarsely across fields. The detail of qualitative work goes in the `qualitative` extension below;
  - the `formal` profile: formal claims are a core claim type.
- **Moved with lab record v0.20:** the codebook, codings and counts of coded material, in the lab record and in the claimset, are the `qualitative` extension. Captured sources, private sources and pseudonyms stay in the lab record's core: any desk research reads outside material.
- **Still to come in the lab record:** extensions for physical data collection (samples, instrument runs, survey waves, interview sessions), which the lab record does not record today; the extensions of §3 that concern work in the lab (`wet-lab`, `ecology-field`, `survey`) would bring them.

## 3. Candidate extensions

Priority **1**: wanted for the field trials and the first months after launch. **2**: common kinds of work, wanted soon after. **3**: specialised, written when the first papers need them.

### 3.1 Trials, health and evidence synthesis

| Extension | Adds | Main objects | Follows | Priority |
|---|---|---|---|---|
| `trial` | Registration identifier and phase; arms (definitions) and allocation ratio; randomisation, allocation concealment and blinding; endpoint hierarchy (primary, secondary, exploratory, safety) on outcome variables; analysis sets (intention-to-treat, per-protocol); stopping rules and interim analyses; harms; the participant flow table | evidence, definition, claim, table | CONSORT 2025, SPIRIT, WHO ICTRP | 1 |
| `systematic-review` | Protocol registration; search strategy (sources, dates, search strings); eligibility criteria; screening counts (the PRISMA flow); the included studies (references) with their risk-of-bias judgements by domain; certainty of evidence per outcome; meta-analysis model, heterogeneity (I², τ²), small-study tests | evidence, table, reference, claim | PRISMA 2020, PROSPERO, Cochrane RoB 2, ROBINS-I, GRADE | 1 |
| `observational-health` | Study design (cohort, case-control, cross-sectional), exposure and outcome ascertainment and windows, follow-up, confounder set (and its causal diagram as an artefact), missing-data handling, routinely collected data sources | evidence, definition | STROBE, RECORD, TARGET (target trial emulation) | 2 |
| `diagnostic-accuracy` | Index test and reference standard, thresholds, the 2×2 counts, sensitivity, specificity and predictive values with intervals, spectrum of patients | evidence, table | STARD 2015, QUADAS-2 | 2 |
| `prediction-model` | Development or validation, outcome horizon, predictors, discrimination and calibration measures, recalibration, decision curves | evidence, definition | TRIPOD+AI, PROBAST | 2 |
| `case-report` | Timeline of the case, presentation, diagnostic assessment, intervention, follow-up and outcomes, the patient's perspective, consent to publish | evidence, table | CARE | 2 |
| `health-economics` | Perspective, time horizon, discount rates, costs with currency and price year, health outcomes (QALYs, DALYs), incremental ratios, uncertainty analyses | evidence, definition | CHEERS 2022 | 3 |
| `animal-study` | Species, strain (with RRID), sex, age and weight; housing and husbandry; randomisation, blinding and sample-size justification; humane endpoints; welfare approval | evidence, definition | ARRIVE 2.0 | 2 |
| `veterinary-clinical` | As `trial` and `observational-health` for animal patients: species, owner consent, clinical setting | evidence | CONSORT and STROBE-Vet | 3 |

### 3.2 Behaviour, society and measurement

| Extension | Adds | Main objects | Follows | Priority |
|---|---|---|---|---|
| `behavioural-experiment` | Manipulations and their checks; stimuli and counterbalancing; exclusion rules and attention checks; participant pool, recruitment platform and compensation; deception and debriefing; power analysis | evidence, definition, artefact | APA JARS-Quant, the 21-word statement | 1 |
| `survey` | Population and sampling frame, sampling design, mode, response and cooperation rates by standard definition, weights and nonresponse adjustment, questionnaire items and their wording | evidence, definition, artefact | AAPOR Standard Definitions, DDI | 1 |
| `psychometrics` | Structure of a scale (items, subscales), reliability (α, ω, test–retest), validity evidence, measurement invariance, scoring | definition, evidence | COSMIN, Standards for Educational and Psychological Testing | 2 |
| `qualitative` (registered, v0.1: codebooks, codings, counts) | To add: researcher positionality; data collection detail (mode, duration, who collected); sampling to saturation and how it was judged; intercoder agreement; member checking; the analytic memo trail | evidence, definition, artefact | COREQ, SRQR, JARS-Qual | 1 |
| `identification` | Causal identification in observational data: difference-in-differences, instrumental variables, regression discontinuity, synthetic control; the assumptions each needs and their tests (parallel pre-trends, first-stage strength, bandwidth); clustering of standard errors | evidence, claim | the econometrics and epidemiology literature; no single standard | 1 |
| `economic-model` | Calibrated or estimated structural models: calibration targets, parameters and their sources, counterfactual experiments | evidence, definition | — | 3 |
| `replication` | The original study and claims replicated, protocol fidelity and deviations, success criteria fixed in advance, many-site structure | evidence, claim | FORRT, the Many Labs designs | 2 |
| `preregistration` | Details of a registration beyond the core anchor: registry, identifier, stages of a registered report (in-principle acceptance), the list of deviations and transparent changes | claim, evidence, paper | OSF, AsPredicted, Registered Reports | 2 |
| `education-intervention` | Setting (schools, grades), unit of assignment, implementation fidelity, dosage | evidence | What Works Clearinghouse standards | 3 |
| `hci-study` | Tasks, apparatus and prototypes, usability instruments, think-aloud protocols | evidence, artefact | — | 3 |

### 3.3 Life sciences

| Extension | Adds | Main objects | Follows | Priority |
|---|---|---|---|---|
| `wet-lab` | Replicate structure (biological and technical); reagents and their validation (antibodies, primers, plasmids with RRIDs); cell line authentication and mycoplasma testing; uncropped originals of blots and gels; image acquisition and quantification; blinding of quantification | evidence, definition, artefact, result | MDAR framework, MIQE (qPCR) | 1 |
| `omics` | Assay type and platform; data deposits (GEO, SRA, ENA, PRIDE, MetaboLights accessions); reference genome and annotation versions; pipeline and normalisation; feature-level results and gene sets; multiple-testing control | evidence, artefact, table | MINSEQE, MIAME, MIAPE, FAIRsharing | 1 |
| `genetic-association` | Cohorts and ancestry, genotyping and imputation, association models, loci and lead variants, summary statistics deposits | evidence, table, artefact | STREGA, GWAS Catalog | 2 |
| `structural-biology` | Deposits (PDB, EMDB), method (X-ray, cryo-EM, NMR), resolution and validation statistics, model quality | evidence, artefact | wwPDB validation | 2 |
| `bioimaging` | Modality, instrument, objective, channels and fluorophores, pixel and voxel size, processing pipeline, image deposits | evidence, artefact | REMBI, OME, BioImage Archive | 2 |
| `neuroimaging` | Modality (fMRI, EEG, MEG, PET), acquisition parameters, preprocessing, atlases and coordinate spaces (MNI), data in BIDS | evidence, artefact, result | BIDS, COBIDAS | 2 |
| `ecology-field` | Sites (with core locations), sampling effort and design (plots, transects, traps), occurrence records, detection probability, taxa, collection permits | evidence, definition, table | Darwin Core, GBIF, Ecological Metadata Language | 1 |
| `taxonomy` | Descriptions of taxa: nomenclatural acts and their registration (ZooBank, IPNI, MycoBank), type and voucher specimens (institution and catalogue numbers), diagnoses, phylogenetic placement | claim, definition, artefact | ICZN, ICN, Darwin Core | 2 |
| `agronomy` | Field trials of crops and livestock: plots and blocks, cultivars and breeds, soil and weather, management | evidence, definition | MIAPPE | 3 |
| `microbiology` | Strains and culture collections, growth conditions, antimicrobial susceptibility (breakpoints) | definition, evidence | EUCAST, CLSI | 3 |

### 3.4 Physical sciences and engineering

| Extension | Adds | Main objects | Follows | Priority |
|---|---|---|---|---|
| `synthesis` | Structures of compounds (SMILES, InChI, molfile artefacts); reactions (reactants, reagents, catalysts, solvents, conditions, scale); yields; characterisation of each compound (NMR, HRMS, IR, melting point) and purity; substrate-scope tables by structure | definition, evidence, table, result | Open Reaction Database, IUPAC InChI, RXNO | 1 |
| `spectroscopy` | Technique and instrument, acquisition conditions, peak lists with assignments, spectra in open formats | evidence, artefact, table | JCAMP-DX, nmrML, mzML | 2 |
| `crystallography` | Small-molecule and materials structures: CIF files, deposition numbers (CCDC, COD, ICSD), refinement statistics | artefact, evidence | IUCr CIF | 2 |
| `materials` | Composition, structure, processing history and its parameters, property measurements and their conditions | definition, evidence, table | NOMAD and Materials Project metadata | 2 |
| `atomistic-simulation` | Level of theory (functional, basis set), force fields, software and convergence settings, structures computed | evidence, artefact | NOMAD metainfo | 2 |
| `particle-physics` | Experiment and data-taking period, energy and integrated luminosity, event selection and signal regions, background estimation, systematic uncertainty breakdown, likelihoods and HEPData records, limits and their method (CLs) | evidence, table, result, artefact | HEPData, pyhf likelihoods | 1 |
| `astronomy` | Targets (SIMBAD, NED identifiers), celestial coordinates and epochs, telescopes, instruments and filters, archive identifiers (MAST, ESO, …), photometric systems | definition, evidence, artefact | IVOA standards | 2 |
| `earth-observation` | Sensors and platforms, spatial and temporal resolution, reference systems, processing levels, products | evidence, artefact | ISO 19115, STAC | 2 |
| `climate-modelling` | Model and configuration, experiment and scenario identifiers (CMIP), ensemble members, forcing data, output variables | evidence, definition | CMIP data request, CF conventions | 2 |
| `environmental-sampling` | Sampling locations and times, sample handling, detection and quantification limits, QA/QC (blanks, spikes, replicates), units of concentration | evidence, result, table | EPA and ISO sampling standards | 2 |
| `metrology` | Full uncertainty budgets (Type A and B components, sensitivity coefficients), calibration and traceability | result, evidence | GUM (JCGM 100) | 3 |
| `engineering-test` | Test standards (ISO, ASTM methods), specimens and their preparation, loading and environmental conditions, failure modes | evidence, definition | ISO and ASTM test methods | 2 |
| `chronometric-dating` | Radiocarbon and other dates: laboratory codes, measured ages, calibration curve and software, calibrated ranges | result, table | IntCal, the radiocarbon reporting conventions | 2 |

### 3.5 Computing, data and AI

| Extension | Adds | Main objects | Follows | Priority |
|---|---|---|---|---|
| `ml-experiment` | Data splits and leakage checks, contamination checks, hyperparameter search space and budget, seeds and repetitions, compute used, model cards | evidence, artefact | REFORMS, Model Cards | 1 |
| `ai-evaluation` | Evaluations of models: benchmark versions and harness, prompts and sampling settings beyond the core, judge models and their agreement, contamination, elicitation effort | evidence, artefact | HELM, the evaluation-reporting literature | 2 |
| `forecast-evaluation` | Forecast origins, horizons and frequency, the evaluation scheme (rolling or fixed origin), point and probabilistic error measures and their scaling, benchmark data versions | evidence, definition | M-competition practice | 2 |
| `optimisation-benchmark` | Problem instances and libraries, solvers and versions, time and memory limits, hardware, performance profiles | evidence, table | — | 3 |
| `software` | Software as the contribution: functions provided, tests and their coverage, performance benchmarks, dependencies, release and archive identifiers | artefact, evidence, claim | CodeMeta, Software Heritage, the Research Software Directory | 2 |
| `dataset` | Data as the contribution: collection process, composition, labelling, intended uses, maintenance, versions and identifiers | artefact, evidence | Datasheets for Datasets, Croissant, DataCite | 2 |
| `security` | Threat model, affected systems and versions, vulnerability identifiers (CVE), disclosure timeline | claim, evidence | CVE, the coordinated disclosure practice | 3 |
| `proof-assistant` | Proof assistant and version, libraries and their versions, axioms used, absence of unproved holes | evidence, artefact | Lean, Coq, Isabelle conventions | 2 |

### 3.6 Humanities, law and texts

| Extension | Adds | Main objects | Follows | Priority |
|---|---|---|---|---|
| `archival` | Archive, collection, shelfmark and folio; dates of documents with their uncertainty and calendar; provenance and custody; transcription conventions; digitised images (IIIF) | definition, artefact, result | EAD, IIIF, ISAD(G) | 1 |
| `textual-scholarship` | Witnesses and their sigla, variant readings (an apparatus), stemmatic relations, edition principles | definition, table, artefact | TEI | 3 |
| `legal` | Jurisdiction, court and instance, decision and legislation identifiers (ECLI, ELI) with their date of validity, legal sources' hierarchy, doctrinal method | definition, reference, evidence | Akoma Ntoso, ECLI, ELI | 2 |
| `corpus` | Corpora and their composition, size in tokens, sampling, annotation layers and schemes, tokenisation, frequency normalisation | definition, evidence, artefact | CLARIN, Universal Dependencies | 2 |
| `linguistics` (registered, v0.1) | Interlinear glosses, segmentation, acceptability judgements; to add: language varieties (Glottolog), elicitation and judgement-task detail | result | Leipzig Glossing Rules | registered |
| `archaeology` | Sites, contexts and stratigraphy, finds and their registration numbers, typologies, survey and excavation methods | definition, evidence, table | ARIADNE, CIDOC CRM | 2 |
| `art-history` | Objects (museum identifiers), attributions and their certainty, provenance chains, technical analyses | definition, claim | CIDOC CRM, Getty vocabularies | 3 |
| `practice-research` | Design and practice-based research: the artefact made, the design rationale, iterations, evaluation with users or critics | artefact, evidence | — | 3 |

## 4. Order of work

1. Run the field trials: one real paper each for `synthesis`, `wet-lab`, `trial`, `behavioural-experiment`, `ecology-field`, `particle-physics` and `archival`, converted under the core alone, recording what the core cannot hold.
2. Bring the needs of §1 that the trials confirm into the core, in one version, before the platform goes live: that version is the launch baseline.
3. Write the priority-1 extensions from what the trials needed beyond the core, each with its test files.
4. Write further extensions as papers that need them arrive; a paper needing an unregistered extension records what the core can hold, and is corrected when the extension exists.
