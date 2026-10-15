# Room glossary

One word for one thing. Use these words when writing to Zero, and spell out the meaning the first time in a message if the reader may not know it. "Avoid" lists words that blur two different things.

## Running the room

| Word | Meaning | Avoid |
|-|-|-|
| **Stop** (one agent) | Cancels that agent's current turn and clears its queue. Nothing new starts until **Resume**. | pause, halt |
| **Stop all** | Stop for every agent at once. It also prevents automatic installs. | pause all |
| **Resume** | Allows new requests again after Stop. It doesn't re-run what Stop cancelled. | restart, unpause |
| **Quit** | Closes the desktop app. That stops the room: active turns are cancelled, queued work is cleared, goals pause. Saved chats and tasks are kept. | close, stop |
| **Turn** | One run of an agent, from the request that woke it to its reply. | session, run (when talking to Zero) |
| **Silent turn** | A turn that finished without a chat reply. The room says "finished without replying". | skipped turn |

## Releases

| Word | Meaning | Avoid |
|-|-|-|
| **Release** | A numbered version of the room's server and web page, such as 0.12.72. | build, update (for the room) |
| **Desktop shell** | The Windows app that hosts the room. Its builds are named R5, R6, R7. A shell build and a release are separate things. | release (for the shell), desktop release |
| **Stage** | Prepare a release on disk and check its files, without installing it. | build, package |
| **Rehearsal** | A trial run of a staged release in a throwaway room, with no agent turns. | test run, dry run |
| **Install** | Make a staged release the one the room runs. Running turns get 30 seconds first. | deploy, ship |
| **Rollback** | Return to the previous installed release. | revert, undo |
| **Manifest** | The list of every file in a release and its hash. Shortened to its first 12 characters, it's also the release's ID (for example b3308757b5ae). | package hash |
| **Receipt** | The record a stage or install writes: what was done, the hashes, and the result. | log, report |

## Work and review

| Word | Meaning | Avoid |
|-|-|-|
| **Task** | One card on the board, with an owner and a next action. | ticket, issue |
| **Donor** | A change packaged as a patch file for the release composer to apply. | PR, diff |
| **Review** | A request to a named peer to approve or reject one exact version of a change. | check, look |
| **Follow-up** | A tracked question or next step assigned to one participant. | ping, reminder |
| **Live check** | The owner checking a feature on the installed release, as Zero would use it. | acceptance (alone), QA pass |
| **Normal launch** | Zero starting the app by double-clicking it, as his everyday account, without admin rights. Elevated or scripted runs aren't a normal launch. | real launch |
