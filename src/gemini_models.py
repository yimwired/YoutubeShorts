"""Gemini text models used by research and the scriptwriter.

Research and scripts sit on different generations on purpose:

* Research needs Google Search grounding, and the free tier grounds only on
  the 2.5 family (500 requests a day, shared by flash and flash-lite). On
  every 3.x flash model the free-tier grounding row reads "Not available",
  and a grounded call answers 429 RESOURCE_EXHAUSTED from the first request.

* The scriptwriter needs no tools, so it runs on 3.x. Free-tier quotas are
  counted per project per model (GenerateRequestsPerDayPerProjectPerModel),
  which takes the script calls out of the 2.5-flash quota that the Moon
  pulse exhausts every morning on the same project.

The 2.5 family is closed to projects created after the 3.x launch (404
"no longer available to new users"), so this split only works on a key
from a project that predates it -- which is the one GEMINI_API_KEY holds.

Pinned versions rather than the `-latest` aliases: an alias can change
model under a running pipeline, and a prompt tuned on one model is a
request to the next.
"""

RESEARCH_FLASH = "gemini-2.5-flash"
RESEARCH_LITE  = "gemini-2.5-flash-lite"

SCRIPT_FLASH = "gemini-3.8-flash"
SCRIPT_LITE  = "gemini-3.5-flash-lite"
