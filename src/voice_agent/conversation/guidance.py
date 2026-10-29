"""Question-specific answer requirements, without customer-facing reply templates."""


def response_requirements(question):
    requirements = {
        "topic": question.relevance,
        "can_skip": question.optional and question.kind != "consent",
    }
    if question.kind == "rating":
        requirements.update(
            format="integer", minimum=question.minimum, maximum=question.maximum,
            clarification_goal="Acknowledge relevant feedback and politely invite the customer "
                               "to choose their own number within these bounds.",
            acceptance="An explicit relevant number, or explicit confirmation of one pending "
                       "number grounded in an exact endpoint of the current question. "
                       "Never infer a score from general praise or criticism.",
        )
    elif question.kind == "choice":
        requirements.update(
            format="one_of", choices=list(question.choices),
            clarification_goal="Resolve only the ambiguity between the actual choices. "
                               "Ask politely for a choice when needed; do not request a number.",
            acceptance="An unambiguous choice or its semantic equivalent, normalized to one "
                       "of the supplied choice strings.",
        )
    elif question.kind == "consent":
        requirements.update(
            format="explicit_permission", affirmative_value=question.choices[0],
            clarification_goal="Resolve uncertainty about willingness to participate without "
                               "pressuring the customer or assuming permission.",
            acceptance="Explicit permission only. A greeting, company question, or unrelated "
                       "answer does not grant consent. Refusal ends participation.",
        )
    else:
        requirements.update(
            format="relevant_text", max_length=question.max_length,
            clarification_goal="Ask politely for only the missing detail relevant to this "
                               "question; do not demand a number or invented choices.",
            acceptance="Relevant feedback may be brief, positive, or negative.",
        )
        if question.kind == "conditional_text":
            requirements.update(
                format="relevant_details_or_denial",
                insufficient_answers=list(question.insufficient_answers),
                acceptance="A clear denial or relevant description satisfies this question. "
                           "An affirmative without the requested details needs clarification. "
                           "Unrelated personal details do not satisfy its relevance rules.",
            )
    return requirements
