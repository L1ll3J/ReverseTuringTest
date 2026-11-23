# Black and white sitting-room study

This is an illustrative visual study, not a playable demo. It imports no game modules and makes no model, API, microphone, audio, profile, or save calls. The dialogue and room descriptions are authored illustration. Item inspection shows artwork, not implemented item effects.

## Composition

Black is the main surface. White identifies the active speaker, a selected room, or a focused control. Thin neutral-grey rules divide space without dashboard cards. The main wordmark preserves the original AlibAi casing and bundled Press Start 2P letterforms. The menu uses the same sitting-room renderer as the discussion, with a static lamp, a table and dim anonymous silhouettes. It contains only the logo and Play, Items and Quit actions. There are no taglines, invented place names, dialogue, character labels or speaking indicators on the idle menu.

The discussion puts the cast across a dim sitting room above stationary, readable dialogue. A coarse moving cone of white light reveals the current speaker. Their head and shoulders move by one logical pixel while their line is being spoken. The light fades afterwards; the words remain readable. Cast positions are shuffled only by Play, once per illustrative run. The silent menu and discussion share those positions. Changing lines or visiting other screens does not shuffle them.

Reduced motion keeps a static speaker highlight and removes movement. Pixel lettering is reserved for names, headings and menu controls. Body copy uses native readable text. Native Qt buttons, lists, scroll areas, focus indicators, a modal inspection window, and keyboard navigation remain available.

The room screen uses a sparse floor plan with five native room choices. Item inspection deliberately puts all 25 current canonical coloured item sprites against black, including Revolver and Watch this Drive. The exact source assets include this worktree's horn for Throw Voice, pressure disc for Booby Trap, faceless locket for Dark Whisper, and dark red Love Potion. Both `_candidate` alternatives are excluded. Names are an illustrative artwork mapping, not a statement about availability, unlocks, or implemented mechanics. No stale preview directory or cached prototype asset root is used.

## Run

```powershell
& 'C:\Users\Josh\AppData\Local\Temp\alibai-gameplay-stability\.venv\Scripts\python.exe' 'C:\Users\Josh\AppData\Local\Temp\alibai-gritty-study\design\gritty_study.py' --assets 'C:\Users\Josh\AppData\Local\Temp\alibai-gritty-study'
```

The default asset root is the parent of this study's `design` directory. `--assets PATH` can override it explicitly. The pixel font and all 25 displayed item images are validated before opening the study. Readable body text uses Segoe UI, Arial, DejaVu Sans or Liberation Sans. On Windows, Segoe UI and Arial are explicitly loaded from the existing Windows Fonts directory so offscreen rendering does not fall back to the pixel font.

The earlier exterior-house composition and its optional background-image control are removed from this iteration. No artwork is generated or downloaded.

## Controls

- Play starts a new illustration and shuffles the cast once. Items opens the artwork inspection and Quit closes this study window.
- House, Rooms and Conversation navigation appears after entering. It remains available directly through Alt+1, Alt+2 and Alt+3.
- Listen or Space advances through authored lines without moving the body text.
- I opens Pockets. Up and Down select an object in the scrolling list of 25 artworks. Home and End reach the first and last objects. Escape closes the one modal window.
- Alt+1, Alt+2 and Alt+3 select House, Rooms and Conversation.
- Tab and Shift+Tab move between native controls. Enter and Space operate focused buttons.
- Reduced motion removes speaker movement and moving light.

There are no simulated kills, votes, item uses, unlocks, costs or AI responses. The study does not open or close any earlier prototype windows.

## Export

```powershell
& 'C:\Users\Josh\AppData\Local\Temp\alibai-gameplay-stability\.venv\Scripts\python.exe' 'C:\Users\Josh\AppData\Local\Temp\alibai-gritty-study\design\gritty_study.py' --assets 'C:\Users\Josh\AppData\Local\Temp\alibai-gritty-study' --export 'C:\Users\Josh\AppData\Local\Temp\alibai-gritty-menu-v2-renders'
```

Exports use a fixed cast seed, line, light position and frame. They cover menu, discussion and rooms at 1280x800 and 800x600, plus every one of the 25 current item artworks at both sizes. `item-revolver-1280.png` and `item-watch_this_drive-1280.png` explicitly show those two source sprites in context. Native modal dialogs are separate windows, so `inspection-*.png` captures the inspection itself while `item-*.png` composites that same dialog over its parent scene. The horn, trap, whisper and potion filenames retain their short descriptive names. There are 106 screenshots per full export. Exporting into the source asset tree is rejected.

The new menu and current conversation are exported together into this versioned directory. Earlier screenshots in `alibai-gritty-renders` remain unchanged for review notes.

The study intentionally makes no claim about production integration or live game behaviour. Visual approval should precede any production implementation.
