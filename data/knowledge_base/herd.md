# SOP: Herd health - the animals, and the record that stands in for them

Derived from `docs/sweetwater-ranch.md`. Nothing else in this repo may source these rules,
and no rule here may be invented at the prompt: an agent that cites `HERD-08` when there is no
`HERD-08` is worse than one that says it has no rule to cite.

**The standing order this file serves:** keep roughly 1,000 head healthy and accounted for
across 34,000 acres, and surface a sick, down, dead, or missing animal, or a predator hit,
before it becomes a loss. Cow-calf production is the core of this ranch's revenue and the
band of sheep rides beside it. Every animal is a line on that account.

**What makes this world different, and it shapes every order you write.** An animal cannot be
re-read on demand. A tank can be checked again in five minutes; an observation is a person, at
an animal, on a date, and if nobody made one there is no data at all rather than stale data.
Labor is scarce and the driving distances are brutal, so the value of your work order is in
getting the right person to the right animal while it still matters, and in being honest about
how old the last look was.

**Your surface is the care record.** The Farm API's status for the animal, the observations
written against it, and the care tasks that name it. You cannot read a sensor, and no number
from one is on this page by design. Say what the record shows and name the rest as unknown.

---

## HERD-01 - A dead animal is a loss to record and a cause to find

**Applies to:** `deceased`, severity `critical`.

The Farm API records this animal as deceased. That is a loss on the account already; what the
work order decides is whether it becomes two.

1. **Send a person to the animal, today.** Confirm the death on site, note the condition of the
   carcass and the ground around it, and record what they find as an observation against this
   animal. The record has to say how she died in a way somebody can read next year.
2. **Read the observations on this page for the cause, and quote them as written.** A note that
   names a predator, an injury, or a down animal found at first light is the closest thing to a
   witness you have. Do not paraphrase it into something more certain than it is.
3. **A predator hit is a containment question for the rest of that group.** The ranch mission pairs
   fence and predator together as containment and loss. Say what the note says about the rest of
   the group and about the fence line, and name the pasture so the crew knows where to look.
4. **The rest of the pasture is the next question.** If other animals in the same pasture changed
   state this sweep, this is not one dead cow. Say so, count them, and treat the group as the
   subject.
5. **Carcass disposal is part of the order,** not an afterthought. A carcass inside the fence line
   draws the next predator.

**Escalate to the general manager** on every deceased animal where the note points at a predator,
or where more than one animal in a pasture is affected. One loss is a note in the record; a pattern
is a decision about the pasture, and that decision is not the crew's to make alone.

## HERD-02 - An animal that is off the books but not sold or dead is unaccounted for

**Applies to:** `inactive`, severity `warning`.

`inactive` is the status that is neither `active`, nor `sold`, nor `deceased`. The ranch mission
says every animal is to be accounted for, and this one is not: the record cannot say where she is
or why.

1. **Ask the record first.** The observations and care tasks on this page may explain it: an
   animal moved to a hospital pen, held back at shipping, or noted as missing. Quote what is there.
2. **If the record does not explain it, the order is a headcount, not a search party.** Name the
   pasture she was last recorded in and ask for the group there to be counted against the roster.
3. **Do not diagnose an animal you cannot see.** Unaccounted for is a fact about the record. Sick,
   dead, or strayed are guesses until somebody is standing in that pasture.

## HERD-03 - A high-severity observation is a person asking for a second person

**Applies to:** `observation_high`, severity `critical` for an `injury` or `mobility` observation,
`warning` for the rest.

Somebody looked at this animal and wrote down that what they saw was serious. The observation is
already the diagnosis this order is allowed to have; the order is about what happens next.

1. **Critical (injury, mobility):** a down, lame, or injured animal loses ground by the hour on this
   ranch, where the vet is a drive away. Get a person to the animal today, decide on site whether
   the vet is called, and record the recheck as a care task so it is not lost to the next shift.
2. **Warning (behavior, appetite, appearance, general):** real, and on a clock of days rather than
   hours. Schedule the recheck, name who watches for the change, and say what would move it to
   critical.
3. **Quote the observation as written, with its date.** The person who wrote it is the witness. Do
   not sharpen it, and do not soften it.
4. **Be honest about how old the last look was.** An observation from this morning and one from
   three weeks ago are different facts, and the date is on the page.

## HERD-04 - An overdue care task is a promise the record shows was not kept

**Applies to:** `care_overdue`, severity `warning`.

A care task with a due date in the past and a status of `pending` means somebody said this animal
would be seen by a date and the record does not show that it was. On a ranch this size that is not
negligence; it is how a small crew loses track. The order exists to give it back.

1. **Name the task, the animal, the due date, and how far past it is,** from this page. Those are
   the facts of the finding and they are all on it.
2. **Read the task title against the observations.** A recheck on a down cow that is a month
   overdue is a different order from a shearing that slipped a week. Say which this is.
3. **The order is to do the task or to close it on purpose.** If the record shows it was done and
   nobody updated the task, the fix is the record and you may propose that. If it was not done, the
   fix is a person at the animal, and the task stays open until they are.
4. **Do not invent what happened in the gap.** A month with no observation is a month with no
   data, not a month in which the animal was fine.

## HERD-05 - What is never yours, and what you may never write

**Applies to:** every category in this file, at every severity.

1. **No sensor reading, ever.** Water, feed, fence, power, and weather at this animal's pasture are
   `water_feed`'s and `infrastructure`'s. None of it is on this page, and if a number like that
   appears in your draft it came from somewhere you cannot check. The supervisor joins your order
   to theirs; you do not.
2. **Head counts as a compliance question are `compliance`'s.** You look at animals for their
   health. The roster on this page is for knowing who else is standing in that pasture.
3. **Never fabricate an observation.** A care record outlives the shift, and a note that was not
   made by a person at an animal on a date is a lie in a document people will trust later. If you
   propose recording one, every word in it must come from this page.
4. **A stated absence is a gap, not a zero.** "No observation has ever been recorded for this
   animal" is not "this animal has been fine." Say the first and never imply the second.

## HERD-06 - Sold is not missing

**Applies to:** the animal's status, wherever it appears on this page.

A `sold` animal left the ranch on purpose and is a ranch running normally. Code never opens a
finding on one, and you will never be handed one as an incident. If a sold animal appears among
the herd-mates on this page, it is context and not a concern: do not count it as a loss, do not
send anybody to look for it, and do not fold it into a pattern with a dead one.

## HERD-07 - The record carries the flag, in the words the record already holds

**Applies to:** `deceased` at every severity, and `observation_high` at severity `critical`.

An animal's history has to show that this was found and that somebody was sent, or the next
person reading the record sees a status and a note with nothing between them and the shift that
acted. So the finding is written into the care record, once, and it is the one observation you
may propose recording yourself.

1. **Propose `create_observation` on this animal, type `general`, severity `medium`.** The note
   states what the Farm API records and which observation on this page it rests on, and that a
   person has been dispatched by this work order. Nothing else.
2. **Every word of the note comes from this page.** Quote the status as recorded and the existing
   observation as written, with its date. No condition, cause, or count that is not already on the
   page: HERD-05 still governs, and a note that adds a fact nobody observed is the fabrication it
   forbids.
3. **`observedAt` is a timestamp already on this page:** the incident's first sighting, or the
   Farm API's last update. Never a time you made up.
4. **One note, once.** If an observation on this page already says the incident was flagged and a
   person sent, do not propose another.

The proposal pauses for a human before anything is written. Propose it and move on; do not treat
the record as updated.

---

## The two things never to write in a herd work order

1. **An all-clear.** Code already found this animal's status, observation, or task outside its
   band. "The animal appears healthy" from a page you cannot see the animal from is a claim nobody
   made and the crew will believe.
2. **A number from a sensor.** Not a tank level, not a temperature, not a fence voltage. If you find
   one in your draft, it is not from this page. Take it out and name the question as unknown.
