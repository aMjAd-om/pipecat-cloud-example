# Ministry of Labour visit feedback

Questionnaire: `questionnaires/ministry-of-labour.ar.json`.
Registered telephone agent: `ministry_labour_feedback`.
Visit supplied by the operator: Thursday morning, **5 November 2026**, in Oman.
No branch, exact time, transaction or customer identity is invented.

After the existing telephone pickup greeting, the introduction is:

> نتواصل معك بخصوص زيارتك الأخيرة لوزارة العمل صباح اليوم الخميس.

It continues with the same consent question used by Bank Nizwa:

> ممكن ناخذ دقيقة من وقتك؟ نحب نعرف رأيك في الزيارة.

The five question texts, order, response types, rating bounds and optional flags
match `bank-nizwa.ar.json`: consent, overall rating, rating reason, staff
experience and transaction completion. Relevance guidance refers to the Ministry
instead of the bank. Bank Nizwa's questionnaire and agent remain unchanged.

The introduction does not name the assistant or Data Mining. If explicitly asked
whether automated, the agent answers truthfully. It does not claim to be a ministry
employee or that the Ministry authorized the call.

## Select for telephone calls

In the active `.env` in the application repository, set:

```dotenv
TELEPHONY_AGENT_ID=ministry_labour_feedback
```

This selects the new agent for telephone calls while preserving the application's
other configured agents. Explicit entries in `TELEPHONY_CALL_CONFIG`, if used,
take precedence over the telephone default and must select this agent as well.
Changing only `SURVEY_QUESTIONNAIRE` does not override a selected registered agent.

After pulling the new files and editing `.env`, rebuild and recreate the agent
when no calls are active:

```bash
docker compose -f docker-compose.ubuntu.yml up -d --build --force-recreate agent
```

No Asterisk, Netplan or firewall change is required. The dialer uses the selected
telephone agent on the next call. The questionnaire is dated for 2026-11-05;
update the visit date and the relative phrase «اليوم الخميس» before reusing it
for a different visit/date.
