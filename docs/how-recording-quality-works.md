# How recording quality is measured

This document explains, in plain language, how Study Runner judges the
quality of a recording, how it keeps track of time, and what the words in
`quality.jsonl`, `timing.jsonl` and the session browser actually mean.

It is written for people who run studies, not for programmers. There is no
code in it. If you want the code, `study_runner/contracts/quality_journal.py`,
`study_runner/contracts/session_lifecycle.py` and
`study_runner/contracts/recording_checkpoint.py` carry the same
explanations next to the implementation.

---

## 1. Why quality is measured *during* the recording

The obvious way to check a recording is to wait until it is finished and
then look at the files. Study Runner used to do exactly that, and it has one
fatal weakness: **a session that never finishes leaves no evidence at all.**

Those are precisely the sessions you most want to know about. The laptop
lid closed, a sensor lost its Bluetooth connection, the battery died, the
participant left early. Under the old approach, the recording that went
wrong was the one that could not tell you what went wrong.

So quality is now observed *while the data flows past*, and every
observation is written to disk immediately. If the machine is switched off
mid-session, the observations made up to that second are already on disk.
The file is written one line at a time and never rewritten, so an
interrupted write can cost you the last line, never the earlier ones.

Two files are produced at the top of every session folder:

- **`quality.jsonl`** — things that might be wrong with the data.
- **`timing.jsonl`** — facts about clocks, kept separate because they are
  not defects; they are the record of how time was measured.

Both are plain text, one entry per line. You can open them in any text
editor.

---

## 2. The four numbers, and what each one really tells you

### Sample count

How many measurements arrived. The simplest number, and the one worth
checking first: if a sensor that should produce 250 measurements per second
delivered 4,000 over a twenty-minute session, nothing else in the report
matters yet.

### Effective rate

The rate the sensor **actually** achieved, in measurements per second.

This is deliberately *measured*, not copied from the sensor's
specification. A device advertised as 250 Hz that is quietly running at
243 Hz because the machine is overloaded is a real finding — and it is
invisible if you trust the label. Both numbers are recorded: the advertised
one (`nominal_rate_hz`) and the measured one (`effective_rate_hz`), so you
can compare them.

It is computed from the first and last measurement's own timestamps and the
number of measurements in between — not from a stopwatch on the recording
computer.

### Gaps

A **gap** is a stretch where measurements should have arrived and did not.

For a sensor that reports 100 times per second, one measurement is expected
every 0.01 seconds. Study Runner flags a gap when the actual wait was more
than **three times** that expected spacing. Three, rather than one, because
computers are busy: a measurement arriving 0.011 seconds late instead of
0.010 is normal scheduling noise on any laptop, not a transport failure.
Three missed intervals is late enough that something genuinely stalled.

That threshold lives in a **quality profile**, and the profile carries a
version number that is stamped into every entry. This matters more than it
looks: if the threshold is tightened next year, old sessions do not silently
get re-judged by rules that did not exist when they were recorded. Each
recording says which rulebook judged it.

**Sensors without a fixed rate cannot have gaps.** Event markers — "trial
started", "button pressed" — arrive when something happens. A quiet hour
between two markers is the study working as designed, not a defect, and it
is never reported as one.

### Jitter

Jitter is how *unevenly* the measurements are spaced. A sensor with 4 ms
between every pair of measurements has zero jitter; one that alternates
between 2 ms and 6 ms averages the same rate but is far less useful for
anything time-sensitive.

Formally it is the standard deviation of the intervals between
measurements: a small number means evenly spaced, a large number means
ragged.

Two deliberate choices sit behind that number:

**Backwards steps are excluded.** If a measurement's timestamp is *earlier*
than the one before it, that is a defect, and it is reported separately as a
**timestamp regression**. It is never folded into the jitter average.
Averaging a fault into a quality score hides the fault twice: the jitter
figure gets worse for a reason it does not name, and the fault itself
disappears into an average.

**The arithmetic had to be replaced.** The textbook way to compute a
standard deviation is to keep a running total and a running total of
squares, then subtract. It is fast, and here it was wrong. The intervals
being averaged are tiny numbers that barely differ from each other — a
250 Hz sensor's intervals are all about 0.004 seconds — and the two large
totals being subtracted are nearly identical. The computer's limited
precision means most of the meaningful digits cancel out, and what is left
is rounding noise.

In testing, this reported **1.3 nanoseconds of jitter on a perfectly even
stream**. The stream had none. The number was fabricated entirely by the
arithmetic — and it was fabricated in exactly the field a researcher would
read as "how good was my timing".

It was replaced with a method (Welford's algorithm) that keeps a running
average and updates it one measurement at a time, never subtracting two
large near-equal numbers. On the same synthetic stream it reports 3
picoseconds — which is not zero either, but that residue is the limit of how
precisely the timestamps themselves can be written down, not an error the
measurement invented. Roughly four hundred times smaller, and honest about
where it comes from.

None of these four numbers requires storing the measurements themselves.
Only the running totals are kept. A recording must not need memory
proportional to its own length just to describe its own quality — an
eight-hour session would otherwise be limited by the report about it.

---

## 3. Two clocks, and why the recording trusts only one of them

Every computer has two clocks:

- The **wall clock** is the time of day. It is the one that is useful to a
  human ("the session started at 14:32 UTC"), and it is the one that can
  jump. Network time synchronisation nudges it. Daylight saving moves it.
  Someone correcting a wrong date moves it by hours. It can move
  *backwards*.

- The **monotonic clock** only ever counts forwards, at a steady rate, from
  an arbitrary starting point. It cannot tell you what time it is. It can
  tell you, reliably, how much time has passed.

Study Runner uses the monotonic clock for **every duration**. If the system
clock jumps thirty minutes mid-session, no measured interval, gap or rate
changes by a single millisecond, because none of them ever consulted the
clock that moved.

The wall clock is used exactly twice per session: **one UTC anchor at the
start, one at the end**. These are what let you say when the session
happened and line it up against other records. They are written into
`timing.jsonl` as anchors, plainly labelled, so a later reader knows they
came from the clock that can move rather than from the clock that cannot.

### Detecting a jump

Every second, both clocks are read and compared against the previous
reading. If ten seconds passed on the monotonic clock, ten seconds should
have passed on the wall clock. If the wall clock instead advanced by
seventy, it jumped — and a **clock jump** is written to `quality.jsonl` with
the size and direction of the move.

The threshold is one second: below that is ordinary drift and
correction, above it is an event. Backwards jumps are caught the same way.

The recording does not *depend* on this, precisely because durations use the
monotonic clock. But it still has to be on record, because the session's two
UTC anchors were read from the clock that moved. Knowing a jump happened
between them is the difference between an anchor you can trust and one you
cannot.

---

## 4. Capture delay: when the measurement actually happened

A timestamp says when the recording computer *received* a measurement. That
is not when the sensor *took* it. Between the two sit the sensor's own
processing, a Bluetooth or USB transfer, and the operating system's
scheduling — anywhere from well under a millisecond to a substantial
fraction of a second, depending on the device.

Each stream therefore declares a **capture delay**, and — more importantly —
declares **how well that figure is known**. That honesty label is one of
four values:

| Label | Meaning |
|---|---|
| `measured` | Someone measured this delay on this hardware. Trustworthy. |
| `datasheet` | Taken from the manufacturer's documentation. Usually right. |
| `estimated` | A considered guess. Use with care. |
| `unknown` | Nobody knows. Any alignment claim rests on nothing. |

Anything that does not state a delay gets `unknown` rather than zero.
Recording a guess as a fact is worse than recording that you do not know: a
zero looks like a measurement, and silently promotes an unexamined
assumption into your data.

This matters most when aligning two sensors. If one stream's delay is
`measured` at 12 ms and the other's is `unknown`, the alignment between them
is not accurate to 12 ms — it is accurate to an amount nobody has
established. The label is what lets you see that before you build an
analysis on it.

---

## 5. Where a session is, overall

Three separate files each answer their own question about a session: is the
recorder running, has the finalisation job run, did the data validate. All
three are useful, all three stay separate, and none of them answers the
question an operator actually asks — *where is this session?*

That single overall answer is the **lifecycle state**, derived from those
three files. It is derived on reading, never stored, so it cannot drift out
of step with the files it describes.

| State | Meaning |
|---|---|
| `IDLE` | Not started. |
| `PREFLIGHT` | Checks are running before recording begins. |
| `RECORDING` | Data is being captured. |
| `FINALIZING` | Capture is over; files are being assembled and checked. |
| `SEALED` | Finished, and the data validated. |
| `WITHDRAWN` | Consent was withdrawn. |
| `FAILED` | Something stopped and needs a person. |

Three rules govern this, and each exists to prevent a specific wrong answer:

**A failed upload never un-seals valid data.** Upload status is deliberately
not an input. If a remote server is down, or a share is full, or credentials
expired, that is a problem with the destination — not with the scientific
data, which was captured and validated correctly. Uploads have their own
status and are reported separately. Letting a network problem downgrade
validated data would be exactly backwards.

**`SEALED` means the data validated**, not merely that a job finished. If
the finalisation job completed but validation marked the data invalid, the
session is reported as still `FINALIZING` — it needs attention, and calling
it sealed would hide that.

**Consent can be withdrawn from anywhere.** `WITHDRAWN` is reachable from
every other state, including from `SEALED` months later, and nothing is
reachable from it. That is the only such state, and it is deliberate.

`SEALED` otherwise never goes backwards — re-running a delivery step does not
re-open validated data. `FAILED` can return to `FINALIZING`, because
retrying a stopped job is a real thing an operator does.

One detail worth knowing: sessions recorded before this machinery existed
have no finalisation file, but they do carry a `COMPLETE.json` or
`ATTENTION_REQUIRED.json` marker. That marker is read as a fallback. Without
it, the example study shipped with the application reported itself as
`IDLE` — a visibly finished session claiming it had never started.

### What happens when someone withdraws consent

To withdraw a session, open it in the admin interface and use the
**Withdraw consent** button on its detail page. You have to type the
session's id to confirm — there is no accidental single click that deletes
a participant's data, and this is checked again on the server, not only in
the browser.

Withdrawing consent deletes the session's data: the recordings, the results,
the quality journals, the logs, and the separate copies of the trial-by-trial
journal that live outside the session folder. It also stops any upload still
waiting in the queue and deletes that queued copy too — a pending upload
holds its own copy of the data and would otherwise publish it minutes later.

**The folder itself stays**, holding a single `WITHDRAWN.json` file and
nothing else. This is deliberate. A session folder that simply disappeared
would be indistinguishable from data loss, and the point of this whole system
is that nothing disappears quietly. What remains is a tombstone: it proves a
withdrawal happened, and it carries no data about the participant.

Two honest limits are worth knowing.

**Anything already uploaded cannot be recalled by this program.** Once a file
is on another organisation's server, only that organisation can delete it.
Study Runner will not pretend otherwise: the destinations that already
received data are **listed by name** in the tombstone, so you know exactly
where to go and what to ask for. If you ever see software claim it deleted
data from a remote service it does not control, be suspicious of it.

**The folder's location still contains the participant identifier**, because
the folder path is how a session is filed. Removing that would mean removing
the folder, which would make the withdrawal invisible again. It is a
trade-off between two kinds of erasure, and this is the side it falls on.

A withdrawal can also be interrupted — the machine restarts halfway through —
and picked up again where it stopped. The record of its progress is kept
*outside* the folder being emptied, for the plain reason that a checklist
stored inside the thing you are deleting does not survive the deletion.

---

## 6. How much of the recording is actually on the disk

When a program writes a file, the data does not go to the disk right away.
The operating system holds it in memory for a while and writes it out when
convenient. That is normally a good thing — it is why computers are fast —
but it means that after a power cut, "the program wrote it" and "it is on
the disk" are two different statements.

Study Runner therefore pushes the recording all the way to the physical
disk every few seconds, and — this is the part that is new — **writes down
where it got to**. Those notes go into `checkpoints.jsonl`.

The order matters more than anything else here:

1. Samples are written.
2. The data is forced onto the disk.
3. A checkpoint is written saying "everything up to here is on the disk".
4. That checkpoint is itself forced onto the disk.

Because step 4 happens after step 2, a checkpoint that survives a crash is
a promise that the data underneath it survived too. If the order were
reversed, a surviving checkpoint could vouch for samples that never landed
— which is worse than having no checkpoints at all, because it would be a
confident false statement instead of an honest gap.

### The unconfirmed tail

Everything recorded *after* the last surviving checkpoint is the
**unconfirmed tail**. It is usually fine: the operating system very
probably wrote it. But "very probably" is not a claim this project makes
about scientific data.

So when a crashed recording is resumed, Study Runner writes an
`unconfirmed_tail` entry into `quality.jsonl` naming the segment, the last
confirmed sample count for each sensor, and when that confirmation was
made. It is a **boundary, not a loss** — it tells you where to look, and it
turns the previously invisible question ("how much of the last few seconds
made it?") into something written down.

A recording that stops normally writes a closing checkpoint after the file
is fully on disk, so it confirms the whole segment and produces no warning
at all. This is deliberate: a warning that also fires on healthy sessions
is one people learn to ignore.

### When the computer cannot keep up

Sensor data arrives into a holding buffer, and the recorder empties it. If
the recorder cannot empty it fast enough — a slow disk, an overloaded
machine, too many sensors at once — the buffer fills, and the transport
layer then **silently discards the oldest data**.

That loss is genuinely invisible at the point it happens. Downstream it
shows up as a gap in the measurements, which looks exactly like a sensor
that stopped sending. The recorder would then report the sensor's fault for
something the computer did.

So the buffer's fill level is watched directly. When it passes a quarter
full, a note is written saying how full it is and how much it can hold;
when it drops back, another note says it cleared. Only those two — a note
every few seconds throughout a bad episode would flood the file during
precisely the minutes you most need to read it.

The highest level ever reached is also recorded in every summary, even when
nothing was ever flagged. "The buffer never went past 2% full" is the
positive evidence that the machine kept up, and it can only be observed
while recording — never reconstructed afterwards.

---

## 7. Journals never stop a recording

If the quality journal cannot be written — the disk filled, the folder
became read-only, a permission changed — **the recording continues**. The
failure is swallowed silently by design.

The reasoning is one-directional: the journal is *evidence about* the
recording. Losing the evidence is bad. Losing the participant's session
because the notes about it could not be filed is worse, and cannot be
undone. The participant is in the chair now.

At regular checkpoints the journal is not merely written but **flushed
durably** — pushed past the operating system's cache onto the physical disk.
Between checkpoints a sudden power loss can cost the last few entries; a
clean stop or a crash of the application alone costs nothing.

---

## 8. Where this shows up without opening a file

Reading `quality.jsonl` by hand is not the normal way to see this. The
session list in the admin interface shows a **recording quality** summary
for each session, boiled down to one of four words:

- **Not measured** — this session was recorded before this measuring
  existed, or its data was withdrawn. Not the same as "clean": nobody
  looked, so nothing can be said either way.
- **Clean** — a journal exists and nothing worth flagging happened.
- **Warnings** — gaps, a clock jump, or a period where the computer fell
  behind were recorded. Worth a look, not necessarily a problem.
- **Needs attention** — a timestamp went backwards, or part of the
  recording could not be confirmed as saved (see the section above). Look
  at this one.

Alongside the word, a short list names what was actually found — "3 gaps in
*EEG*", for instance — so you do not have to guess what triggered the
label. This is deliberately the *only* detail shown here: no chart, no raw
jitter numbers, no list of every individual event. Anyone who wants that
opens `quality.jsonl` itself, using the sections above to read it.

## 9. What you can check yourself

Open a session folder. If `quality.jsonl` is there:

- **No `gap` lines** — no stall long enough to flag was detected.
- **No `timestamp_regression` lines** — no measurement claimed to happen
  before the one preceding it.
- **No `clock_jump` lines** — the system clock behaved for the whole
  session.
- **No `ingest_backlog` lines** — the computer kept up with the sensors.
- **No `unconfirmed_tail` lines** — nothing crashed mid-recording.
- **The `summary` lines** carry the running totals per stream. The last one
  for each stream is that stream's final tally.

A journal containing nothing but `summary` lines is a clean recording. That
is intentional: events are only written when something actually happened, so
a good session leaves a small file rather than a large empty one.

---

## See also

- `sensors-and-data.md` — where the data comes from and what ends up in
  the result files.
- `plugin-recording-architecture.md` — the recording pipeline itself.
