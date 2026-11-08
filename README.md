# LeadVaani

Build, stress-test and self-improve Hinglish phone agents, on Gnani AI.
Built for the Gnani AI Great Indian AI Internship Challenge.

**Build an agent.** Describe the business in one sentence ("a D2C brand confirming COD orders before shipping") and LeadVaani writes the agent: why she's calling, the goal, the Hinglish greeting, the details to collect, and the keyterms Prisma should favour. Ready-made agents: solar lead qualifier, COD order confirmation (cuts RTO returns), clinic appointment desk.

**Live call.** Your voice goes to **Gnani Prisma v3.0** (`format=codemix`, hi-IN + en-IN, keyterms, topic and the agent's last line as context). The model decides the next line and fills the lead card. **Gnani Timbre v2.5** speaks it as **Poorvi**. Short fillers play the moment you stop talking, so there's no dead air.

**Test lab.** Poorvi calls AI customers: an impatient Mumbai professional, a Hindi-only Nagpur uncle who haggles, an angry repeat customer, an off-topic student, an elderly caller who mishears. Each customer line is spoken in its own Timbre voice, optionally squeezed into an 8 kHz noisy phone line, and transcribed back by Prisma. Poorvi answers what Prisma heard, not the clean text, so the whole speech pipeline is tested. The screen shows what the customer said next to what Prisma heard, with a script-insensitive word match (so "mandir" and "मंदिर" count as the same word).

**Scorecard and self-fix.** After each call a reviewer model scores goal, language match, brevity, empathy and honesty, plus measured numbers (Prisma word match, details collected, think time). It proposes rules; one click teaches them to Poorvi (a new agent version), and the score history for each customer shows whether she got better.

**Run (Windows)**
1. Copy `.env.example` to `.env`. Add your Gnani API key, and a free Groq key (console.groq.com/keys) for one-second replies. The test lab needs the Groq key; without it Hermes is used (about 25 s per reply).
2. Double-click `start.bat` (or `py -3 app.py`), open http://127.0.0.1:8800, allow the microphone.

Standard-library Python only. Test without keys: `set MOCK=1` then `py -3 app.py`.
