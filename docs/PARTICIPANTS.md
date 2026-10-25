# Taking part in HiveNet testing

Thank you for lending your PC. This page explains what the program does, how it protects your computer,
what is collected and what to watch out for. Please read it before installing.

## What it does

The **bee** program (the `bee-node` folder) asks the HiveNet server for small pieces of work ("cells"),
processes them and sends back the result. It works **only** during the hours and at the intensity you choose in its
panel (`http://127.0.0.1:7777`, reachable from your PC only), and it rests on its own while you are using the PC,
while the PC is busy with other programs or while a laptop is on battery (you can change these choices).

In return you receive **honey**: internal test credits with no monetary value that cannot be transferred.

## How it protects your PC

- Computation runs as **WebAssembly**: a format that cannot read or write files, use the network, see the screen or
  launch programs.
- It runs in a **separate process**, without access to your environment variables, with limited memory and a time
  limit. If it overruns, the operating system shuts it down: nothing is left running on your PC.
- The computation modules are **signed** by the server: a tampered module is rejected. Modules that declare no
  memory limit or that ask the system for functions are rejected too.
- The bee talks **only to HTTPS servers** (an encrypted connection). For tests on a home network, the organiser will
  tell you whether `HIVENET_ALLOW_INSECURE=1` is needed; in that case the bee prints a warning. It must never be used
  over the internet.
- The bee's panel only answers your own PC, not other machines on the network nor the websites you visit.
- The program uses no external libraries to download: there is only the code you can see in the folder.

## What is sent to the server

| Sent | Why |
| --- | --- |
| Username and password (the password is only stored as an unreadable hash) | To sign in |
| Operating system and program version | To know which bees are compatible |
| The result of every cell, sealed for the server | It is the work done |
| For every cell: phase timings, bytes exchanged, number of texts, CPU time, memory used by the bee program, number of CPU cores | To measure where time is lost and improve the network |
| The IP address you connect from (any server you connect to can see it) | For the limits against abuse |

**Stay on your PC and are never sent:** whether you are using the PC, the load from other programs, the battery
status, and your local statistics and metrics (in the `.hivenet` folder in your home directory).

## What to watch out for

1. **Start the bee with your normal user account, never as administrator or root.** The sandbox protects the PC,
   but it is good practice for a program that receives work from the network to have as few permissions as possible.
2. **Keep Node.js up to date** (at least 22.23.2, 24.18.1 or 26.5.1). A flaw in the Node engine is the only way a
   module could escape the sandbox.
3. **Only use the server address the organiser gives you.**
4. **Do not share the `node.json` file** in the `.hivenet` folder: it contains your bee's token (it is already
   readable by your user account only).
5. **Electricity and heat:** the bee uses CPU, and therefore electricity. The panel shows an estimate of the cost
   (adjustable with your PC's power draw and your electricity price). If the PC gets too warm, lower the intensity.
6. **What you can see:** the bee processes texts submitted by other participants. During testing **only
   non-sensitive texts** are used; do not try to read or keep them, and if you submit jobs of your own, also use
   non-sensitive texts only.
7. **This is a test:** the service may stop, test data may be deleted and honey may be reset.

## How to pause or remove it

- **Pause:** the **Pause** button in the bee's panel (it stops within 2 seconds).
- **Stop it:** Ctrl+C in the terminal where it runs (it hands back the work it has not started).
- **Remove it completely:** stop it, click **Unlink** next to your bee in the HiveNet web panel, then delete the
  `bee-node` folder and the `.hivenet` folder in your home directory.

## If something goes wrong

Open the bee's panel, click **Download report** and send it to the organiser together with a description of what
happened. The report only contains timings, byte counts and general characteristics of the PC (system, processor
model and number of cores, total memory), never the texts that were processed.
