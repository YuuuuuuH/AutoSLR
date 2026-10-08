# AutoSLR: From Human Review Methods to Evaluated AI Assistance

## What a Systematic Literature Review Is For

A researcher investigating a technology needs more than a list of relevant papers: which findings agree, under what conditions, and how strong is the evidence? A systematic literature review (SLR) addresses a defined question by systematically finding, selecting, evaluating and synthesising primary studies. It can establish the benefits and limitations of a method, explain inconsistent results and identify research gaps. Its methods and decisions should be inspectable by other researchers.

AutoSLR is a master's project investigating AI assistance for this work in computer science. The proposed research question is whether coordination between document acquisition, evidence extraction and verification can improve evidence correctness and reduce expert checking effort. To evaluate that claim, we first need to understand what human reviewers do and what existing automation studies have actually measured.

## How Human Reviewers Conduct a Review

To answer research questions in a particular field, such as which findings corroborate one another, under what conditions these findings hold, and how strong the supporting evidence is, human reviewers first define the research question and review protocol. The protocol sets out the publication date range, search sources, inclusion and exclusion criteria, data extraction items, quality assessment and synthesis. These choices determine which evidence can answer the research question. Researchers conduct a pilot and record subsequent procedural changes. Guidelines in software engineering divide this work into three phases: planning the review, conducting the review and writing the report.

Reviewers then translate the research concepts into search terms, search suitable databases and supplement discovery by tracing references and papers that cite the retrieved studies. They need to record the search queries, search dates and search results, remove duplicates and identify related reports. Screening titles and abstracts retains potentially eligible papers. Reviewers obtain full texts from repositories, publishers or authorised library access; they then assess the full texts against the review protocol to decide whether to include them. Every step needs to be documented, including the reasons for excluding or including a paper. If a paper cannot be obtained, this is recorded as an acquisition problem rather than classified as a failure to meet the eligibility criteria. Cochrane requires two independent reviewers to make final full-text inclusion decisions in its medical reviews; in computer science projects, the team should agree on appropriate checking arrangements with their supervisors.

For included studies, reviewers extract information using a form that has been piloted in advance. Each extracted item needs sufficient context for interpretation, such as the measured outcome, value, unit, experimental conditions and supporting passages or tables. Independently extracting critical or subjective outcome data and then resolving disagreements helps control interpretation bias. Related publications must be handled together to avoid counting the same study more than once.

Critical appraisal focuses on the trustworthiness of the findings. For a review comparing computational methods, a checklist can be used to assess the suitability of baselines, the reporting of experimental conditions, reproducibility and known limitations. For example, two methods that both report a 50% reduction in memory use may differ in model size, sequence length, batch size, precision or hardware configuration, and their quality assessment metrics may also differ. These experimental conditions must be made explicit alongside the results before reviewers can make evidence-based comparisons.

Finally, synthesis brings together the findings of individual studies to answer the research question. The final report should present the research methods, screening process, comparative findings, limitations and unresolved uncertainties. Readers should be able to trace conclusions back to the underlying evidence.

## Which Work AI Can Assist, and What Has Been Demonstrated

AI assistance can be introduced in query construction, literature discovery, candidate study assessment, contextual information extraction, source verification and synthesis drafting. These stages involve substantial repetitive work in text comprehension, judgement and information organisation, so we can investigate whether AI can take on some of this work and reduce researchers' workload. The key question is: which tasks can be made more efficient while maintaining acceptable quality?

### AgentSLR

AgentSLR implements an epidemiological review workflow in Python, taking literature through retrieval, abstract screening, document conversion, full-text screening, extraction and report generation. A central driver coordinates these stages, each of which can also be run or resumed separately. Retrieved documents are shared across runs, while decisions and extracted results are stored by model, so different models can be compared using the same material. [Workflow driver](https://github.com/OxRML/AgentSLR/blob/main/main.py), [repository documentation](https://github.com/OxRML/AgentSLR)

The reference data for this work come from reviews conducted by the Pathogen Epidemiology Review Group (PERG).

| Count | What it represents | Role in evaluation |
|---|---|---|
| 60,233 article records | The total literature records in PERG's human-conducted reviews, after deduplication and removal of records with empty abstracts | Establishes the size of the original reference corpus |
| 16,248 article records | Records for which AgentSLR obtained full texts and matched PERG's human screening labels, including both inclusions and exclusions | Assesses whether AI screening decisions agree with human decisions |
| 3,808 parameter records | Epidemiological parameters extracted from papers by human reviewers | Assesses whether AI finds and correctly extracts the corresponding data |
| 687 transmission-model records | Information on disease transmission models compiled by human reviewers; these are not large language models | Assesses whether AI correctly identifies and describes the transmission models used in the papers |
| 189 outbreak records | Outbreak information compiled by human reviewers | Assesses whether AI correctly extracts information about the corresponding events |

To acquire the literature, AgentSLR loads predefined Boolean searches for each pathogen and sends the corresponding queries to OpenAlex, PubMed and Europe PMC. The program then reconciles duplicate records using identifiers and bibliographic information, retrieves available open-access PDFs and records download outcomes.

Retrieved articles are screened using prompts that combine the review objectives, predefined inclusion and exclusion criteria, article content and the required output format. Abstract screening first retains potentially relevant articles. These articles then undergo full-text screening, which applies stricter requirements to the converted documents to identify extractable quantitative information. Exclusions include literature reviews, meta-analyses and case studies involving fewer than ten infected individuals. These decisions reflect the scope of the epidemiological review, so applying the system to another topic requires that topic's review protocol and screening prompts.
Within the scope of this paper, the authors divide data extraction into three categories: epidemiological parameters, transmission models and outbreaks. The system first identifies whether relevant information is present in each category, then calls extraction tools with defined fields and schema checks. It saves the resulting parameter records, model descriptions and outbreak records as structured data for subsequent analysis.
Report generation is based on programmatically generated reports, figures, tables and a content manifest. A language model uses these materials to draft a narrative, then critiques and revises it using the supplied evidence packet. Intermediate critiques and revisions are saved alongside the final Markdown and PDF reports.

The paper evaluates screening and extraction performance against PERG annotations. Screening performance is measured using macro precision, recall and F1 across three routes: AI abstract screening followed by AI full-text screening, human abstract screening followed by AI full-text screening, and direct AI full-text screening. Extraction performance is assessed separately in terms of detection of relevant data, the number of extracted records and field correctness. Since an article may contain multiple records, predicted records are matched to reference records from the same article before their fields are scored.

### LEADS

LEADS develops a specialised language model to assist with literature search, eligibility assessment and evidence extraction. The model is obtained by instruction-tuning Mistral-7B-Instruct-v0.3, and its released Python interface covers six tasks: query generation, eligibility assessment, and extraction of study characteristics, treatment-group information, participant statistics and trial results. Each function accepts the relevant question or document content and returns a task-specific output. [Model documentation](https://huggingface.co/zifeng-ai/leads-mistral-7b-v1), [task interfaces](https://github.com/RyanWangZf/LEADS/blob/main/leads/api.py) Its performance may fall short of that of current general-purpose models, although this would need to be tested. We could also investigate whether simple task-specific instructions can make an already capable general-purpose model more efficient on these tasks.


## How to Evaluate Work That Includes Judgment

A published human-conducted systematic literature review (SLR) can provide a reference research question, candidate literature pool, eligibility decisions and data extraction tables. We must first establish which papers have been labelled, including both exclusion and inclusion labels, and align dates, versions and metadata. A list of included papers alone cannot establish that all other papers are ineligible or establish complete retrieval recall. A list of papers labelled as excluded is therefore also important.

For eligibility assessment, precision measures the proportion of papers included by the system that are judged eligible by the reference standard. Positive-class recall measures the proportion of all reference-eligible papers in the defined candidate pool that the system retains.

For data extraction, evaluation should consider values, units, experimental context and source evidence together. Equivalent representations and numerical tolerances should be agreed in advance. Assigning a correct value to the wrong experiment constitutes an evidence error.

Efficiency evaluation can compare experts working alone, AI-assisted work and AI-only work on comparable tasks, counting all time spent reading, checking and correcting. Elapsed time and cost should be reported separately. The time-saving proportion is `(human-only time − AI-assisted time) / human-only time`, while the AI-only time ratio is `AI-only time / human-only time`. The study protocol should specify experimental units and account for repeated measurements from the same reviewer.
