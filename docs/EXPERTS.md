# Join Kagaz as a verified expert (CAs and lawyers)

Kagaz users pay for an expert review of their reply to a notice. The Kagaz
Marketplace agent sends each paid job to a verified expert and pays them per
job through the Fetch.ai Agent Payment Protocol. You run your own agent on
Agentverse and answer jobs from ASI:One chat.

## 1. Set up your agent

```bash
git clone https://github.com/aryanty4gi/kagaz-ai && cd kagaz-ai
pip install -r requirements.txt
```

Create a `.env` with:

```bash
KAGAZ_EXPERT_MODE=human
KAGAZ_EXPERT_SEED=<a long random secret only you know>
KAGAZ_EXPERT_NAME="CA Ananya Rao"
KAGAZ_EXPERT_PROFESSION=CA                 # or Lawyer
KAGAZ_EXPERT_CREDENTIAL=123456             # ICAI membership no. / Bar Council no. e.g. D/1234/2015
KAGAZ_EXPERT_SPECIALTIES=gst_notice,it_143_1,it_148   # or "all"
KAGAZ_EXPERT_FEE_FET=0.4                   # your fee per review (max 0.5)
KAGAZ_EXPERT_LANGUAGES=en,hi
KAGAZ_EXPERT_PIN=<a PIN to log in on chat>
KAGAZ_MARKETPLACE_ADDRESS=<Kagaz marketplace address from kagaz-ai-wrgn.onrender.com>
```

Notice types: `traffic_echallan`, `it_143_1`, `it_139_9`, `it_148`,
`electricity_disconnection`, `sarfaesi_13_2`, `gst_notice`, `court_summons`,
`school_fee`.

## 2. Start it and connect the mailbox

```bash
python -m agents.expert_agent
```

Open the inspector link from the log, click **Connect -> Mailbox**. Your agent
now has its own Agentverse profile and registers with the Kagaz Marketplace.

## 3. Get approved

Your status starts as **pending**. Kagaz checks your number on the ICAI or
Bar Council register and adds your agent address to `KAGAZ_VERIFIED_EXPERTS`.
After that you receive jobs that match your specialties.

## 4. Answer jobs on ASI:One

Chat with your own agent on asi1.ai and send `login <your PIN>`. When a job
is paid you get the notice and the client's draft. Reply:

```
review <case_id>: <your review>
```

Your fee is paid to your agent before you see the job. If you don't pick a
job up within 2 minutes, it goes to the next expert (or the AI-assisted desk),
so clients are never left waiting.
