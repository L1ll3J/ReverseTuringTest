# Cutaway house interaction study

This standalone proof uses the actual generated `house-cutaway-v1.png`. The source PNG and its provenance in `HOUSE_ARTWORK.md` remain unchanged. This is not the game's main menu or night phase. It imports no game modules and uses no model, audio, microphone, profile, save or arrival system.

## Geometry and controls

One uniform scale fits the full 1122x1402 artwork inside the available canvas. The image is centred with black side margins and never stretched independently on each axis. Native masked buttons follow the attic's roof and gable, the kitchen, the library's bay, the basement, and the cellar's vault. The exterior cellar stair and door form a second part of the same Cellar button. The central hall, internal stair, front entrance, sky, grounds and outer roof shingles are not selectable.

Hover or focus reveals a compact, literal-text observation helper in the side margin. Tab and Shift+Tab traverse native controls. Arrow keys move spatially between available rooms. Enter, keypad Enter and Space select once. A selected room emits its ID only; the caller owns room entry, rewards and all game state. Unavailable rooms remain accessible as disabled buttons and cannot emit a selection.

No character sprites or occupancy markers are drawn. Bespoke small character art is pending. No placeholder people are substituted.

## Public knowledge contract

`HouseView` accepts exactly five `RoomObservation` values. Each contains availability, observed names, whether other occupants remain unknown, an optional observation time, and an independently observed light state. There is no actual occupant array, hidden count or game-state input.

Unknown does not mean empty. A room can contain observed names and an unknown remainder. The helper says "Others may be present" in that case. A complete empty observation says that no one was observed at that time, rather than asserting that the room must still be empty. Lighting is rendered only from an explicitly supplied observed light state. It is never inferred from actual occupants, hidden counts, or even the number of known names. Unknown lighting leaves the original ambient artwork intact.

The proof's two selectable views are authored public-projection examples. The observed example contains Jerry in the Library at 00:40, with other occupants still unknown and an observed light on. Its Cellar availability and observed light-off value are independent sample facts. These examples do not simulate timed arrivals, loitering, witnesses or NPC memories. Those rules require separate approval.

## Run

```powershell
& 'C:\Users\Josh\AppData\Local\Temp\alibai-gameplay-stability\.venv\Scripts\python.exe' 'C:\Users\Josh\AppData\Local\Temp\alibai-house-study\design\house_study.py' --assets 'C:\Users\Josh\AppData\Local\Temp\alibai-house-study'
```

The window title explicitly identifies this as a standalone interaction study. `--artwork PATH` can select the same-sized artwork, but hit regions are specific to the supplied cutaway. A differently sized or unreadable image is rejected.

## Export

```powershell
& 'C:\Users\Josh\AppData\Local\Temp\alibai-gameplay-stability\.venv\Scripts\python.exe' 'C:\Users\Josh\AppData\Local\Temp\alibai-house-study\design\house_study.py' --assets 'C:\Users\Josh\AppData\Local\Temp\alibai-house-study' --export 'C:\Users\Josh\AppData\Local\Temp\alibai-house-study-renders-v1'
```

Exports include unknown and observed views at 800x600, 1280x800, 2560x1440 and 3440x1440. They use actual native controls over the generated artwork. Export into the source tree is rejected. No existing study screenshots are overwritten by this versioned path.
