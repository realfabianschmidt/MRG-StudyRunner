# DataCore

This is the part of Study Runner that turns sensor signals into a saved
recording file. Everything about starting a recording, writing it safely to
disk, and checking afterwards that nothing is missing lives here.

## Why this is split into two processes

A study runs inside a normal web server (the same one that shows the admin
page and the participant's questions). Web servers can hiccup: a page
reload, a crash, a restart while testing a change. If the actual recording
also lived inside that same process, any one of those hiccups could
interrupt or corrupt the biosignal data being written at that exact moment —
during a real study, that would mean losing part of a participant's session.

So recording is handed off to its own separate, detached program: the
**worker**. It keeps writing to disk on its own, independently of whatever
happens to the web server. The web server — the **host** — just tells the
worker what to do ("start recording this sensor", "stop and save") and reads
the finished file back afterwards to check it's complete.

## The three folders

| Folder | In plain terms |
|---|---|
| **`host/`** | The web-server side. Decides where a session's files go, starts the worker, tells it what to record, and afterwards double-checks that the recording is actually complete and correct. |
| **`worker/`** | The separate program that does the actual writing. It talks to the sensors' live data streams and puts the bytes on disk, safely, a little bit at a time. |
| **`contract/`** | The shared rulebook both sides agree on: what a command looks like, what "recording is confirmed safe on disk" means, what counts as a quality problem. Neither `host/` nor `worker/` is allowed to reach directly into the other's code — they can only talk through what's defined here. |

Each folder has its own README with the full file-by-file detail; this page
is only the map of how the three fit together.

## The one rule that shapes all three

`host/` and `worker/` are never allowed to import each other's code
directly — only `contract/`. This is checked by a test, not just a promise
in a comment: it's what actually guarantees a web-server hiccup can never
reach into the middle of a running recording.
