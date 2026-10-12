# Jev for Systematic Review Screening

Paper: [Title and abstract screening for systematic reviews with Jev, a System One model: comparison with generative large language models](https://doi.org/10.64898/2026.09.25.26364021), by Kentaro Matsui and Yoshikazu Takaesu. Posted on medRxiv on 1 October 2026 as a preprint.

The study examines whether Jev can screen titles and abstracts efficiently. Given review eligibility criteria and an article record, it returns a probability or choice. The decision is whether to retain a record for full-text assessment.

The evaluation uses 4,527 records from two human-conducted reviews of bipolar disorder treatments: light therapy (1,381 records) and adjunctive pharmacotherapy (3,146). Human reviewers retained 26 and 87 records, respectively. These retention decisions provide the reference labels.

The primary analysis uses Jev's yes/no probability interface, Noul, with a prespecified retention threshold of 0.50. A separate comparison uses explicit include/exclude options, Choice, and the same prompts with GPT-5 mini and GPT-6 Astra. At the primary threshold, Jev's sensitivity and specificity were 99.6% and 98.9% for light therapy, and 93.2% and 93.5% for adjunctive pharmacotherapy.

Estimated costs for one screening pass over all records in the Choice comparison were: 

| Model | Cost (USD) |
|---|---:|
| Jev | 0.20 |
| GPT-5 mini | 2.59 |
| GPT-6 Astra | 33.45 |


The repository releases labels, predictions, prompts and analysis code. But its internal record identifiers cannot reconstruct the input texts or directly identify them in OpenAlex.

For AutoSLR, this study offers a design for comparing screening quality and cost. A possible extension is to test the comparison on independent review topics and examine threshold transfer. Its screening results do not establish performance on full-text extraction or final synthesis.
