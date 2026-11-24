# QSODesk

A station logger for the Raspberry Pi with radio control built in, designed
around fast contest logging for ZL and VK operating.

**Version 0.5.** The logger (fast entry line, five ZL/VK contests, crash-proof storage, one radio) plus a **transceiver manager** for several radios, **keypad mapping**, a customisable
**dashboard** and three radio pages: a **waterfall**, **CW** send and decode, and **FT8**. Multi-operator and
remote stations come next; the log already records station and operator for
every contact so nothing needs reworking later.

## Try it

You need Python 3.8 or newer. Nothing else to install.

    python3 run.py

Then open <http://localhost:8073>. On a Pi, other devices on the network can
open `http://<pi-address>:8073`.

With no radio connected QSODesk uses a **simulated radio**, so everything can
be tried on a Mac, PC or Pi straight away. Use the band and mode buttons to
"turn the dial".

First, open **Settings** and enter your callsign and NZART branch number.

## Logging a contact

1. Type the callsign. The entry line changes colour as you type:
   - **green** - workable
   - **amber** - new multiplier
   - **red** - already worked this period on this band and mode
   - **violet** - doesn't count (wrong band, or not VK/ZL in the Trans-Tasman)
2. **Space**, type their serial number.
3. Jock White only: **Space**, type their branch number. If you have worked
   the station before, the branch is already filled in.
4. **Enter**. Logged.

Band, mode, frequency and time come from the radio. RS(T) defaults to 59 or
599. Your next serial is shown large under "You send". **Esc** clears the line.
Click any contact in the log to edit, delete or restore it.

## Events

Each event is its own log with serials starting at 001. A practice Jock White
event is created for you. Use **New event** for a real contest, a
Trans-Tasman practice, or general (non-contest) logging.

Operating periods follow the clock, so you can practise at any time: Jock
White periods are clock hours, Trans-Tasman blocks are two hours.

## Your log is safe

Every contact is written to three places before the entry line clears:

- the database (`data/qsodesk.db`), synced to disk on every contact
- a plain-text ADIF journal (`data/journal.adi`) you can open in any editor
- optionally a **backup folder**, such as a USB stick (set it in Settings)

If the database is lost or damaged, QSODesk rebuilds it from the journal the
next time it starts. Try it: log some contacts, stop QSODesk, delete
`data/qsodesk.db`, and start it again.

If the browser loses contact with QSODesk, keep logging. Contacts are held in
the browser and sent when the connection returns.

Deleted contacts are only hidden, never erased, and their serial numbers stay
used because they have already been sent on air.

## Exports

- **ADIF** - for any other logging program or QSL service
- **Log sheets** and **Summary** (Jock White) - one section per band in time
  order, in the column order the rules ask for, with multipliers flagged
- **Cabrillo** (Trans-Tasman)
- **Journal** - the raw safety copy

## Connecting a real radio

Not yet tested against real hardware - please report what happens.

On a Raspberry Pi: `sudo apt install libhamlib-utils`, plug the IC-7300 or
IC-705 in by USB, and leave the Radio setting on **Automatic**. QSODesk looks
for the radio by its USB name and starts Hamlib for it.

Radio menu settings that matter:

- IC-705: Set > Connectors > CI-V > *CI-V USB Port* = **Link to [CI-V]**
- IC-7300: Set > Connectors > CI-V > *CI-V USB Port* = **Unlink from [REMOTE]**
- Set the CI-V USB baud rate to **Auto** (IC-7300) or **115200**

Any other Hamlib-supported radio: run `rigctld` yourself and choose
"Hamlib rigctld at the address below" in Settings.

## Radios (transceiver manager)

The **Radios** page lists every transceiver QSODesk is connected to, each with
its live frequency, mode and S-meter. Add as many as you like and give each a
name, such as "80 m station".

- **Connections:** automatic (first Icom found on USB), a particular USB radio,
  Hamlib `rigctld` on the network (any Hamlib radio, including one on another
  Pi), or a simulated radio.
- **Plugged-in radios are offered** at the top of the page with an "Add it"
  button.
- **Each logging device picks its own radio**, on the Radios page or from the
  selector on the Logger. Two laptops at two stations log at the same time,
  each following its own radio's band and mode, with separate serial numbers
  per band where the contest calls for it. The radio's name is recorded with
  each contact as the station.
- **Waterfall, CW and FT8** work with one transceiver at a time. Choose which
  on the Radios page or from the selector at the top of those pages.

Tested with simulated radios only. Running two real USB radios on one Pi
(each needs its own Hamlib port and sound card) has not been tried yet.

## Keypad (custom key mapping)

A cheap USB macro keypad, such as the 12-key "12KV2" and "12KV3" pads with
two or three knobs, turns QSODesk into a one-handed contest station. Any
spare keys on a normal keyboard work the same way.

On the **Keypad** page, press "Map a key", press the key (or turn the knob),
and choose what it does:

- send a CW message, with your call, their call, serial and branch filled in
- stop sending, log the contact, clear the entry line, jump to the callsign box
- tune up or down by a step you choose (made for a knob), next band, next mode
- CW speed up or down, switch transceiver, FT8 call CQ or halt, open a page

The map is stored on the Pi and works on every page and every device. A
suggested 12-key layout can be loaded with one button.

Set the keypad's keys to **F13-F24** with the maker's tool first (the keypad
remembers them). Those keys are used by nothing else, so they never clash
with typing. Give each knob direction its own key too, not the volume
setting.

The keypad must be plugged into the computer whose browser you are logging
on. Tested with simulated key presses; not yet tried with a physical keypad.

## Contests and reports

QSODesk scores these contests, each with its own entry line, dupe checking
and score. Start one with **New event** on the Logger; the dialog shows when
the contest runs and links to its rules, and a **Rules** link stays beside the
event name while you log.

| Contest | Exchange | Notes |
|---|---|---|
| Jock White Field Day | RS(T), serial, branch | hourly periods, per band and mode |
| Sangster Shield | RST, serial, branch | 80 m CW, half-hour periods, warns on "twice running" |
| NZART Memorial Contest | RS(T), serial | 80 m, SSB and CW, hourly periods, warns on consecutive contacts |
| ZL Sprints | RS(T), temperature or grid | 80 m, once per mode |
| Trans-Tasman Low-Band | RS(T), serial | two-hour blocks |

The **Reports** page gives:

- **A contest report** for any event: claimed score, breakdown, contacts per
  period, band and mode split, operators and stations, every multiplier
  claimed, the export files, the rules link and how the log is sent in.
- **A personal report** for everyday contacts (all "General logging" events,
  or the whole log): totals, contacts by month, bands, modes, ZL and VK call
  areas, most-worked stations, prefixes and the latest contacts, for all
  years or one.
- **Callsign look-up:** every contact with a station, across all events.
- **The NZART contest list** with a rules link for each, including those
  QSODesk does not score yet (log those as General logging).

"Print / save as PDF" gives a clean black-on-white copy.

Things to check before relying on the new scores:

- **Memorial Contest:** the rules do not say exactly how the SSB and CW
  multipliers combine. QSODesk multiplies total points by SSB multipliers
  plus CW multipliers. Its list of Oceania prefixes is also incomplete.
- **Sangster Shield:** scored from a New Zealand entrant's point of view.
- **Sprints:** your own temperature is not recorded, so it is missing from
  the "sent" side of the exports.
- **Cabrillo** contest names for these are placeholders; check with the
  contest manager.
- "Prefixes" in the personal report are callsign prefixes, not DXCC countries.

## Dashboard

A page of cards for a shack monitor or the club tent: callsign, local time,
UTC, world clock, space weather, magnetic storm watch, an HF band guide, the
radio, contest score and recent contacts.

Press **Customise** to add, remove, reorder or widen cards, choose the places
on the world clock, and retitle any card. The layout is saved on the Pi, so
every device sees the same dashboard.

Space weather comes from NOAA's Space Weather Prediction Center and needs
internet. The Pi fetches it every 10 minutes while a dashboard is open and
keeps the last copy, so offline the cards show the old figures and say so.

- The NOAA feed readers are written from the documented layouts but have not
  yet been run against the live feeds.
- The HF band guide is a rough rule of thumb from solar flux and Kp, not a
  path prediction.

## Waterfall, CW and FT8

All three pages use one audio stream from the radio's USB sound card. With no
radio, a simulator supplies noise and signals so each page can be tried.
Use the links at the top of every page.

**Waterfall** - what the receiver is hearing (the audio passband, about 3 kHz
wide), with the scale shown as radio frequency. In SSB and data modes, click a
signal to tune it to the centre. This is not the radio's wideband band scope;
that needs the scope data over CI-V and is a later job.

**CW** - the decoder follows the strongest signal, or click one on the
waterfall. It works out the sender's speed by itself. To send, type and press
Enter, or use F1-F6 (CQ, exchange, thanks, ...). The exchange macro uses your
next serial number and branch from the logger. On a real radio the text is
sent by the radio's own keyer. Esc stops sending.

**FT8** - pick a band, and stations appear every 15 seconds. Click a station
to call it, or press Call CQ. QSODesk steps through the exchange, stops by
itself, and logs the contact. Transmit also stops after six calls with no
reply. Set your grid locator in Settings first.

What has and has not been tested:

- Tested in a browser against the simulator: the waterfall, CW decoding at
  12-35 wpm, CW sending, and complete FT8 contacts both ways (answering a CQ
  and calling CQ), including the logging.
- On the **simulated FT8 band** the stations and their messages are scripted.
  The signals on the waterfall are look-alikes, not decodable FT8.
- **Real FT8** needs the open-source ft8_lib programs. Run
  `sh tools/install-ft8.sh` on the Pi (it downloads and builds them). That
  script, real decoding and real FT8 transmit are all untested so far.
- **Real audio** needs a Pi or other Linux machine with `alsa-utils`
  (`arecord`/`aplay`). On a Mac the pages run on the simulator only.
- For FT8 transmit, set the radio's *DATA MOD* to **USB** and watch ALC the
  first time: start with the USB MOD level low.

## Things to confirm before relying on the scores

- **Jock White:** home stations (branch 00) and your own branch are scored for
  contact points but not counted as multipliers. The rule that a field
  station's multiplier only counts if it made 50 phone or 20 CW contacts per
  band cannot be known while logging.
- **Trans-Tasman:** block score is contacts x prefixes, where a prefix counts
  once per band per block; the final score is the sum of the blocks. The
  published wording is a little ambiguous.
- **Cabrillo:** the `CONTEST:` name and the category lines should be checked
  against the WIA sample log before uploading.

## Layout

    run.py                  start here
    qsodesk/contests.py     contest rules - add a contest by adding a class
    qsodesk/store.py        database, journal, backup, recovery
    qsodesk/radio.py        simulated radio and Hamlib (rigctld) control
    qsodesk/audio.py        audio from the radio, and the audio simulator
    qsodesk/reports.py      contest and personal reports
    qsodesk/ft8.py          FT8 timing, sequencing and logging
    qsodesk/spaceweather.py NOAA space weather for the dashboard
    qsodesk/server.py       web server and exports
    qsodesk/static/         the web pages (qd.js holds the waterfall and CW decoder)
    tools/install-ft8.sh    installs the FT8 engine on a Pi
    tests/                  python3 -m unittest discover tests
                            node tests/test_cw.js
