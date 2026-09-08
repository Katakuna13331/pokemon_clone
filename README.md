# Pokémon Save Clone Tool

A homebrew project for **Nintendo 3DS and Nintendo Switch** that allows Pokémon to be copied between supported save files while keeping the original source Pokémon unchanged.

The project is designed as a **copy-based transfer and conversion tool** rather than a move-based transfer tool. Source saves are accessed read-only whenever possible, while only the selected destination save is modified.

---

## Supported Games

### Nintendo 3DS

* Pokémon Sun
* Pokémon Moon
* Pokémon Ultra Sun
* Pokémon Ultra Moon

### Nintendo Switch

* Pokémon: Let’s Go, Pikachu!
* Pokémon: Let’s Go, Eevee!
* Pokémon Sword
* Pokémon Shield
* Pokémon Brilliant Diamond
* Pokémon Shining Pearl
* Pokémon Scarlet
* Pokémon Violet

---

## Features

### 3DS → Switch

The 3DS application can read a selected Generation 7 Pokémon and send a copy over the local network to the Nintendo Switch application.

The original Pokémon remains in the 3DS save.

### Switch → Switch

The Switch application supports local copying between supported games.

Examples include:

```text
Let's Go Eevee → Shining Pearl
Shining Pearl → Let's Go Eevee

Sword → Brilliant Diamond
Brilliant Diamond → Scarlet
Scarlet → Sword

and other supported combinations
```

The application performs a compatibility check before allowing the destination to be selected.

---

## Compatibility Preview

After selecting a Pokémon, the application checks each available destination game.

Example:

```text
CHOOSE DESTINATION
==================

Selected Pokemon:

Species: Pikachu
Nickname: Sparky
Dex #025
Level: 42

[YES] Let's Go Pikachu
[YES] Let's Go Eevee
[YES] Sword
[YES] Shield
[YES] Brilliant Diamond
[YES] Shining Pearl
[YES] Scarlet
[YES] Violet
```

If a species or form is not supported by a destination:

```text
[NO] Let's Go Eevee
```

An incompatible destination cannot be confirmed.

For example, a Pokémon such as Turtwig cannot be copied into Pokémon: Let’s Go, because that species does not exist in those games.

---

## Pokémon Information Display

The application displays information about the Pokémon currently highlighted in the box selector.

Information can include:

* Species
* Nickname
* National Pokédex number
* Level
* Gender
* Shiny status
* Original Trainer
* Trainer ID
* Nature
* Ability
* Held item
* Moves
* Individual Values / IVs
* Party status in Pokémon: Let’s Go

Example:

```text
Species: Pikachu
Nickname: Sparky

Dex: #025
Level: 42
Gender: Male
Shiny: NO

OT: Alex
TID: 123456

Nature: Jolly
Ability: Static
Held Item: Light Ball

Moves:
1. Thunderbolt
2. Iron Tail
3. Quick Attack
4. Volt Tackle

IVs:
31 / 20 / 18 / 31 / 26 / 22
```

If the Pokémon has no nickname:

```text
Species: Pikachu
Nickname: ---
```

Species names are loaded from ROMFS name tables. Where supported by the included data, the application selects the appropriate species-name table based on the Nintendo Switch system language.

---

## Pokémon: Let's Go Storage Support

Pokémon: Let’s Go, Pikachu! and Pokémon: Let’s Go, Eevee! use a storage structure that differs from later Nintendo Switch Pokémon games.

The application represents the LGPE storage as:

```text
40 virtual boxes
×
25 slots
=
1000 Pokémon storage positions
```

The interface also displays the underlying flat storage position.

Example:

```text
LGPE STORAGE MODE

40 virtual boxes x 25 slots

Flat storage index: 126 / 1000

Box: 06 / 40
Slot: 01 / 25
```

Pokémon linked to the active LGPE party are identified separately.

Example:

```text
Species: Eevee [PARTY]

LGPE Party Position: 1
```

Party-linked destination slots are protected from accidental overwrite.

---

## Destination Preview

Before writing a Pokémon, the destination save is opened for preview.

The application shows whether the selected destination slot is empty or already contains a Pokémon.

Example:

```text
DESTINATION SLOT

Shining Pearl

Box 04 / 40
Slot 12 / 30

Species: Bidoof
Level: 8

Status: OCCUPIED
A will choose this slot for overwrite.
```

For an empty slot:

```text
Selected slot: EMPTY

Status: READY
```

This allows the user to see what would be overwritten before confirming the copy.

---

## Copy Confirmation

Before modifying the destination save, the application presents a final confirmation screen.

Example:

```text
CONFIRM COPY
============

SOURCE - READ ONLY

Shining Pearl
Species: Pikachu
Nickname: Sparky

Box 03 / Slot 05
Dex #025   Lv.42


DESTINATION

Let's Go Eevee
Box 06 / Slot 12

Compatibility: YES

Source will remain unchanged.

A = COPY NOW
B = cancel
```

---

## Copy Result

After the operation finishes, the application displays a persistent result screen.

Successful operation:

```text
COPY COMPLETE
=============

Shining Pearl
Box 03 / Slot 05
        ->
Let's Go Eevee
Box 06 / Slot 12

The clone was written successfully.
The source Pokemon remains unchanged.

Press A or B to return.
```

If the destination write fails:

```text
COPY FAILED
===========

The destination could not be written.
The source Pokemon remains unchanged.

Press A or B to return.
```

---

## Source Save Protection

Preserving the source Pokémon is one of the main design goals of the project.

### Nintendo Switch

Source saves are mounted using read-only access:

```cpp
fsdevMountSaveDataReadOnly(...)
```

The source save is unmounted before the destination save is mounted for writing.

The program does not intentionally perform operations such as:

```text
setBoxSlot()
save()
fsdevCommitDevice()
```

on the source save.

Only the destination save receives the generated clone.

### Nintendo 3DS

Generation 7 source save access is performed using read operations.

The source Pokémon is extracted and copied rather than removed from its original slot.

---

## Level Handling

The project includes handling intended to prevent converted Pokémon from incorrectly appearing as:

```text
Level 0
```

For local Switch conversions, the existing Pokémon level is preserved.

For Generation 7 network transfers into supported modern formats, the level can be calculated from the Pokémon's experience value and the destination game's growth-rate data.

---

## Save Formats

The project works with several Pokémon data structures depending on the game family, including formats corresponding to:

```text
PK7
PB7
PK8
PB8
PK9
```

Different games store Pokémon and save metadata differently, so the application converts selected Pokémon into a neutral intermediate representation before creating the destination Pokémon structure.

---

# Technologies and Open-Source Projects Used

This project was created using several existing open-source projects and development tools.

## devkitPro

The console applications are built using the **devkitPro** toolchain.

### Nintendo 3DS

Uses:

* devkitARM
* libctru
* 3ds_rules

### Nintendo Switch

Uses:

* devkitA64
* libnx

These provide the low-level homebrew APIs required for graphics, controllers, networking, save mounting, ROMFS access, and application creation.

---

## PKSM-Core

**PKSM-Core** is used for parts of the Nintendo 3DS / Generation 7 Pokémon handling.

It provides existing Pokémon-related data structures and functionality rather than recreating the Generation 7 implementation from scratch.

Project:

```text
FlagBrew / PKSM-Core
```

Additional PKSM-Core dependencies used by the build include:

```text
FlagBrew / memecrypto
imneme / pcg-cpp
```

---

## pkHouse

**pkHouse** is used extensively for Nintendo Switch Pokémon and save-file handling.

The project makes use of pkHouse functionality and data related to:

* Pokémon structures
* PB7
* PK8
* PB8
* PK9
* SaveFile handling
* Sword / Shield saves
* Brilliant Diamond / Shining Pearl saves
* Scarlet / Violet saves
* Let's Go Pikachu / Eevee saves
* species conversion
* personal Pokémon data
* experience tables
* Pokémon checksums
* save checksums
* Pokémon names
* moves
* abilities
* items
* natures
* game-specific storage layouts

Project:

```text
Insektaure / pkHouse
```

The console application uses the relevant core portions of pkHouse and does not use its SDL2 desktop user-interface component.

---

## Python

Python is used by the project installer to:

* generate project files
* download dependencies
* prepare PKSM-Core dependencies
* prepare the required pkHouse source files
* copy ROMFS resources
* generate Makefiles
* apply compatibility fixes
* validate generated project files
* optionally build the 3DS and Switch applications

---

# AI-Assisted Development

Development of this project was also assisted by **OpenAI's ChatGPT**.

ChatGPT was used as a development assistant during several parts of the project, including:

* C++ code drafting
* architecture planning
* debugging compiler errors
* analyzing devkitARM/devkitA64 build problems
* Makefile corrections
* PKSM-Core integration
* pkHouse integration
* Nintendo Switch save-handling logic
* compatibility-checking logic
* LGPE storage handling
* Level-0 debugging
* user-interface improvements
* generation of Python installer scripts
* documentation
* README drafting

The project was iteratively developed and tested by the project author, with ChatGPT providing code suggestions, debugging assistance, explanations, and generated development scaffolding.

AI-generated or AI-assisted code should not be assumed to be error-free. The resulting binaries and save operations should therefore always be tested carefully with backup save files.

---

# Credits

This project would not be possible without the work of the developers and contributors behind:

* **devkitPro**
* **libctru**
* **libnx**
* **PKSM**
* **PKSM-Core**
* **pkHouse**
* **memecrypto**
* **pcg-cpp**
* **OpenAI / ChatGPT**

Special credit belongs to the original authors and contributors of the Pokémon format and save-handling implementations used by this project.

Please consult the licenses included with this repository and the upstream repositories for the exact copyright and licensing conditions of each dependency.

---

# Important Limitations

The application performs **offline structural Pokémon conversions**.

It does **not** reproduce or emulate an official Pokémon HOME transfer.

This distinction is particularly important for backwards conversions such as:

```text
Shining Pearl → Let's Go Eevee
Scarlet → Sword
Scarlet → Brilliant Diamond
```

The tool may create a Pokémon structure that can be stored by the destination game, but that does not mean that the result represents a transfer that Pokémon HOME would officially permit.

The compatibility system currently focuses primarily on structural and species/form compatibility.

It does not guarantee complete legality for:

* moves
* relearn moves
* encounters
* met locations
* origin data
* ribbons
* memories
* HOME trackers
* game-specific flags
* abilities across generations
* held items across generations
* regional mechanics
* Tera data
* AV/EV conversion
* other generation-specific metadata

A Pokémon successfully copied by this project should therefore **not automatically be considered Pokémon HOME legal or competitively legal**.

---

# Back Up Your Saves

**Always make a backup before using this application.**

This is experimental homebrew software that directly works with Pokémon save data.

Keep backups of:

```text
SOURCE SAVE
+
DESTINATION SAVE
```

before performing transfers.

Although the application is designed to keep the source read-only, unexpected bugs, unsupported Pokémon data, corrupted saves, differences between game revisions, or implementation errors may still cause problems.

---

# Disclaimer

This is an independent homebrew and research project.

It is not affiliated with, sponsored by, approved by, or endorsed by:

* Nintendo
* The Pokémon Company
* GAME FREAK
* Creatures Inc.
* Pokémon HOME
* PKSM
* pkHouse
* devkitPro
* OpenAI

Pokémon and all related trademarks are property of their respective owners.

The software is provided for educational, research, interoperability, and homebrew-development purposes.

**Use at your own risk.**
