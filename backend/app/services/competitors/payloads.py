from app.ports.llm import PayloadClass, TaskId

# what each discovery prompt carries; the llm layer routes providers on these classes
PAYLOADS_BY_TASK: dict[TaskId, frozenset[PayloadClass]] = {
    TaskId.competitor_candidate_generation: frozenset({PayloadClass.client_profile}),
    TaskId.competitor_entity_extraction: frozenset({PayloadClass.public_web}),
    TaskId.competitor_relation_classification: frozenset(
        {PayloadClass.public_web, PayloadClass.public_youtube, PayloadClass.client_profile}
    ),
    TaskId.competitor_ownership_adjudication: frozenset(
        {PayloadClass.public_youtube, PayloadClass.public_web}
    ),
    TaskId.competitor_evidence_summary: frozenset({PayloadClass.public_youtube, PayloadClass.public_web}),
}
