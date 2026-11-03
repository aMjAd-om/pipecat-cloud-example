# Bank Nizwa Arabic demo survey

A short, friendly Omani Arabic example about a visit to بنك نزوى. This is a
demonstration, with no official Bank Nizwa affiliation or integration.

To select it, set these values in the app's `.env` and restart the app (recreate
the agent container if using Docker Compose):

```dotenv
DEFAULT_LANGUAGE=ar
SURVEY_CONVERSATION_MANAGER=true
SURVEY_QUESTIONNAIRE=questionnaires/bank-nizwa.ar.json
```

The same questionnaire works in the voice agent and the local text conversation
lab. Telephone pickup already says `السلام عليكم` and waits for a response, so
the questionnaire introduction continues directly without another greeting:

> معك المساعد الذكي في استبيان تجريبي عن تجربتك في زيارة بنك نزوى. هذا عرض تجريبي وغير تابع للبنك.
> ممكن ناخذ من وقتك دقيقة لتقييم تجربتك؟

After consent, the agent asks four questions, one at a time:

1. كيف تقيّم تجربتك بشكل عام من واحد إلى خمسة، بحيث واحد ضعيفة وخمسة ممتازة؟
2. ممكن تخبرني وش السبب وراء هالتقييم؟
3. وبالنسبة لتعامل الموظف معك، كيف كان؟
4. هل قدرت تخلص معاملتك من نفس الزيارة؟

The agent accepts short free-text staff feedback, understands spoken ratings
such as `ثلاثة`, and closes with:

> شكراً جزيلاً على وقتك وملاحظاتك. يومك سعيد.

Participation is voluntary; consent is required and each feedback question can
be skipped. The demo does not request banking credentials or promise to send
feedback to the bank. Existing survey examples remain available.
