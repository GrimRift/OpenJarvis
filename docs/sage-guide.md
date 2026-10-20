# Sage user guide

Where things are in Sage and how to use them. Sage reads this through its
`about_sage` tool (part `how_to`) to answer "how do I ..." questions, so keep
each section short and true to the app. Settings named here are on the
Settings page unless another page is given.

## Pages
- **Chat**: the main conversation. Ctrl+K opens the command palette.
- **Voice**: talk to Sage with the orb.
- **Dashboard**: an overview of usage and activity.
- **Health** (System Health): Sage's own self-check: voice pipeline, models,
  scheduled jobs, connectors. Same checks as asking "check your health".
- **Memory**: what Sage remembers about you. Tabs: Brain (every fact as a 3D
  orb), Facts (the list: pin, hide, forget, View in the Brain) and Removed
  (restore a forgotten fact).
- **Books**: audiobooks Sage narrates from PDF/EPUB files.
- **Data Sources**: connected accounts (Gmail, Outlook, Teams, calendar, Drive).
- **Agents**: Sage's background operators and their runs.
- **Logs**: server logs.
- **Settings**: everything below.

## Talking to Sage (voice)
- Say "Hey Sage" to wake it. You can say the request in the same breath
  ("Hey Sage, any news?"). Settings: Wake word check, Listen after wake word,
  Greet on Wake Word, Speak right after the wake word, Wake word microphone.
- Listen for a follow-up keeps the mic open after a reply so you can carry on
  without the wake word.
- Speak Replies reads answers to spoken questions aloud; Speak Typed Replies
  also reads answers to typed ones.
- Voice: Sage voice and Voice engine pick the voice (local Nano/Turbo voices
  or cloud). Speech-to-Text picks the engine that hears you.

## Talking over Sage
- Listen while Sage speaks: say "stop" to cut an answer; other words are
  judged. Words said over an answer either make Sage carry on, get answered
  at the next pause, or are ignored as overheard talk or a short reaction.
- Turn down while I talk: how far Sage lowers its own voice (the "dip")
  while you speak over it. A slider; 15% by default.
- Listen while Sage works: the mic stays on while an answer is prepared;
  "stop" cancels it.
- Recognise my voice: tells your voice from Sage's own voice heard back.

## Microphone
- Microphone picks the input for conversation; Wake word microphone can be a
  different one just for "Hey Sage".
- Extra microphone boost adds gain for a quiet or distant mic.
- Remove background noise: for conversation only, everything, or off.

## Chat
- Rewind: hover over one of your messages and press the rewind button. The
  chat is cut back to it and the text goes back into the box to edit. Undo
  for 10 seconds; after that Sage also forgets facts it learned from the cut
  turns.
- Attach images or files by dropping them on the chat.
- Bold and quoted parts of answers are highlighted.
- Sage can draw diagrams (Let Sage draw diagrams; Decide on its own when to
  draw) and make pictures (Let Sage make pictures).

## Models
- Prefer cloud model: answers come from the cloud model (faster, stronger)
  and fall back to the local model. Off = everything runs on this PC except
  making pictures.
- Local model / Local Vision Model pick the on-PC models (llama.cpp).
- Ask "which model are you" for the one answering now.

## Memory
- Sage remembers facts about you from conversation. Say "remember that ...",
  "forget that ..." or "what do you know about ...".
- Use memory context decides whether remembered facts go into answers.

## Sage speaking first
- Sage may start conversations / Sage may speak first: reminders, the
  greeting, things it noticed.
- Quiet hours, Not now ("not now" by voice for a quiet spell), Never bring up,
  Tell me when ("tell me when X happens").
- Sage knows when you are here: uses mouse/keyboard activity to know you are
  at the PC.
- Daily greeting: the welcome when you come back.

## Morning brief and reminders
- The morning brief collects mail, Teams, calendar, classes and news on a
  schedule; ask "what's my brief" or "brief me".
- Reminders and scheduled tasks: "remind me at 5 to ...", "every weekday at
  8 ...", "what's scheduled", "cancel the ... reminder".
- Class schedule: "what classes do I have today".

## Media, apps and the PC
- Spotify: play, pause, skip, volume. YouTube and Netflix can be opened and
  played; YouTube ads are skipped.
- Opens apps and games only when asked. Games: tic-tac-toe, chess, ping pong.
  A game opens in a panel over the chat: click or drag chess pieces, click
  tic-tac-toe squares, or say/type the move. Undo, flip the board, difficulty,
  new game as White/Black/Random. Ping pong: mouse moves the paddle, click to
  serve; first to 11 (win by 2) or unlimited (best rally and score kept);
  easy/normal/hard/adapt; say "pause", "resume", "what's the score", "stop".
- Windows: move a window, list monitors, look at what is on screen.
- "How's my PC": CPU, RAM, GPU, disk, opens the live system panel.

## Web and research
- Searches the web for anything current, reads pages, and does Deep
  Research for longer questions. The search engine is chosen in Settings >
  Tools > Search engine: Exa (the default), Tavily or Serper (Google results),
  each with its own key there; Tavily also takes a backup key, used once the
  first is out of credits. If the chosen engine fails or runs out of credits,
  the next one with a key answers (Exa, then Tavily, then Serper) and Sage says
  which. Weather and time by voice; weather opens a
  panel (Use this PC's location).
- Car navigation: briefing and Waze from a phone shortcut.

## Audiobooks
- Import a PDF/EPUB on the Books page or by voice; Sage renders chapters with
  its narrator voice. Keep chapters ready ahead renders before you reach them.
- Play, pause, export to M4B, send to the phone.

## Phone
- Telegram: talk to Sage from the phone; pictures and audiobooks can be sent
  there.

## Desktop app
- Runs in the tray; Ctrl+Alt+S brings it up. Start with Windows is a setting
  (currently off by the user's choice).
