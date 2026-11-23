# Item sprite provenance

Generated with the built-in `image_gen` tool, one request per asset, with `transparent_background=true`. The PNG files preserve the generated alpha channel. No CLI fallback or image post-processing was used.

The final prompt for each PNG is the shared prefix below followed by one space and its subject text. Sprites are displayed using nearest-neighbor scaling in Qt.

## Shared prefix

```text
Use case: stylized-concept. Asset type: a single inventory sprite for a retro pixel-art murder-mystery game. Style: match chunky 32-bit game weapon icons, clearly visible square pixels, restrained 4-to-6-tone shading, dark brown charcoal outline, strong simple silhouette, no smooth gradients or antialiasing, no glossy 3D rendering. Composition: exactly one item, centered on a square transparent canvas with generous empty padding, whole object visible; no frame, text, label, scene, floor or cast shadow. Designed to remain legible at 48 to 64 pixels on a black UI. Primary request:
```

## banana.png

```text
a ripe, delicious golden yellow banana with a gentle curved silhouette, small brown stalk, a few warm ochre shaded pixels.
```

## muffle.png

```text
a stylized closed mouth covered by a muted purple cloth gag tied in a small knot, just the isolated mouth-and-cloth symbol, no head or face.
```

## spare_paper.png

```text
three ivory paper voting ballots fanned slightly, the front sheet bearing one simple dark red X mark, small curled corner, no writing or letters.
```

## guardian_angel.png

```text
a small protective angel charm: two ivory feathered wings surrounding a warm gold circular halo and tiny blue central gem; readable simple symmetrical silhouette.
```

## Remaining original-item set

Generated with the built-in `image_gen` tool, one request per asset, with `transparent_background=true`. These sprites provision artwork for the requested original items; their presence does not imply that the mechanics are implemented. Original generated PNGs were copied without image post-processing.

Each final prompt is this shared prefix followed by one space and the corresponding subject below.

```text
Use case: stylized-concept. Asset type: one inventory sprite for a playful retro pixel-art murder-mystery game, part of the AlibAi item set. Style: crisp chunky square pixel clusters, a small deliberate palette with 4 to 6 shade families, warm charcoal outline, simple readable silhouette, like a well-drawn 48px game item enlarged without smoothing. Composition: one centered icon on a genuinely transparent square canvas, complete object visible with balanced padding. Legible at 64 pixels on dark charcoal UI. No lettering, words, numbers, frame, scene, floor, soft glow, photorealism, smooth gradients, or glossy 3D rendering. Subject:
```

### throw_voice.png

```text
a small expressive wooden theatrical mask in warm brown and ivory with a single teal speech curl emerging beside its mouth, suggesting borrowed speech.
```

### dark_whisper.png

```text
an ivory ghostly skull inside a small smoky teal speech bubble, eerie but comical rather than frightening.
```

### love_potion.png

```text
a small round pink glass potion bottle with a cork, a single crimson heart-shaped liquid center and two tiny bright pixels for sparkle.
```

### booby_trap.png

```text
a cartoon game trap item: a closed, compact old-fashioned iron jaw trap with a brass pressure plate, shown as a simple toy-like inventory symbol, no diagrams or separate parts.
```

### soul_transfusion.png

```text
two small head-and-shoulder silhouettes, one amber and one teal, linked by one ivory ghost-shaped curved arc, suggesting a soul moving between people.
```

### lucky_coin.png

```text
one thick gold coin with a half-ivory skull face and half-small-green-clover motif, a readable symbol of equal luck and danger.
```

### spin_the_dagger.png

```text
a short silver fantasy dagger with a brown grip surrounded by one broken ochre circular motion arrow, a compact spinning-dagger icon.
```

### presidential_decree.png

```text
a rolled ivory proclamation tied with a red ribbon and a single gold wax seal, a pompous official document with no writing.
```

### mass_discombobulator.png

```text
a whimsical little brass gadget with one large blue spiral lens, crooked antenna and two violet spark pixels, a silly memory-scrambling invention.
```

### occult_ritual.png

```text
three short violet candles with warm tiny gold flames around one simple dark purple ritual ring, a compact magical inventory token without lettering.
```

### revolver.png

```text
a stylized old western revolver game icon in side profile, short silver-gray barrel and dark brown wooden grip, toy-like chunky pixels, no ammunition, no diagrams or realistic technical detail.
```

### forged_note.png

```text
a folded ivory note with a dark feather quill resting diagonally across it and a small red seal, no legible writing.
```

### watch_this_drive.png

```text
a bold small red target marker pierced by a tiny gold forward arrow, with a short checkered pennant, suggesting marking a target to earn a second action.
```

### divine_revelation.png

```text
a small open ivory eye inside a warm gold sunburst, two short straight gold rays descending from it, a benevolent private revelation symbol.
```

### mail_in_ballot.png

```text
a cream envelope with a small ballot corner showing one dark red X and a blue postage-stamp square, no words or other writing.
```

## Refined artwork and utility set

Generated with the built-in `image_gen` tool, one request per asset, with `transparent_background=true`. Files were copied unchanged from the generated-image directory. Four requested replacements update their existing paths; the earlier versions remain in Git history. Soul Transfusion and Occult Ritual alternatives use `_candidate` filenames and are not selected automatically. The attempted Revolver revision produced no output asset, so `revolver.png` retains its earlier version.

The exact prompt for each file is the shared prefix below followed by one space and its subject text. The existing item sprite was the edit target for replacements and candidates; `presidential_decree.png` supplied a quality/style reference. Utility sprites used that decree as a style reference.

```text
Detailed, beautifully drawn 32-bit pixel-art inventory sprite for AlibAi, a warm, slightly theatrical murder-mystery game. Deliberate crisp pixel clusters, nuanced material shading and a coherent limited palette, strong readable silhouette at 64px. More crafted and evocative than a flat pictogram or toy sticker. Whole object centered on a genuinely transparent square canvas with generous consistent padding. No text, labels, diagrams, background, frame, cast shadow, glossy 3D render, soft-focus edges or photorealism.
```


### love_potion.png (replacement)

```text
Image 1 is the edit target. Image 2 is only a reference for the material quality and deliberate pixel detail; do not copy its subject. Replace the plain round heart bottle with a beautiful antique apothecary love potion: elegantly faceted ruby-rose glass vial, a refined curved stopper, fine warm brass collar, deep pink liquid and just a small heart motif in the glass. Rich shape and light through glass, restrained rather than cute.
```


### dark_whisper.png (replacement)

```text
Image 1 is the edit target. Image 2 is only a reference for the material quality and deliberate pixel detail; do not copy its subject. Replace the smiling skull speech bubble with a beautiful spectral keepsake: an open tarnished silver mourning locket, midnight-teal interior, a very faint pale human profile suggested in a thin whisper of vapor emerging from it. Wistful and mysterious, not a cartoon mascot, no happy skull or speech-bubble outline.
```


### throw_voice.png (replacement)

```text
Image 1 is the edit target. Image 2 is only a reference for the material quality and deliberate pixel detail; do not copy its subject. Replace the theatrical mask entirely with a compact antique brass speaking horn, gracefully flared bell, dark polished mouthpiece, and one delicate turquoise ribbon of projected voice curling from its opening. A convincing beautiful magical object for throwing one's voice, no mask or human face.
```


### booby_trap.png (replacement)

```text
Image 1 is the edit target. Image 2 is only a reference for the material quality and deliberate pixel detail; do not copy its subject. Replace the bear trap entirely with a fictional room-wide magical pressure trap: a squat aged dark-iron disc with warm brass edging, a sealed crimson crystal inset and restrained etched geometric inlay. A coherent ominous little floor device powerful enough to affect a room, no jaws, clamps, real explosive components or assembly drawing.
```


### soul_transfusion_candidate.png (candidate)

```text
Image 1 is the edit target. Image 2 is only a reference for the material quality and deliberate pixel detail; do not copy its subject. Create an alternate more evocative Soul Transfusion sprite: two small antique soul phials, one holding amber light and one teal light, with an ivory spirit-ribbon visibly passing between them. Delicate faces suggested in the colored light, refined brass necks, a beautiful singular magical artifact composition rather than generic avatar pictograms. Keep it compact and legible.
```


### occult_ritual_candidate.png (candidate)

```text
Image 1 is the edit target. Image 2 is only a reference for the material quality and deliberate pixel detail; do not copy its subject. Refine this ritual into a more beautiful compact occult object: three uneven violet-black wax candles with carefully drawn drips and warm tiny flames on a weathered circular metal ritual tray, restrained violet inlay and a few ash-colored details. Keep the existing candle-ring concept, improve craft and material depth, no readable runes or cartoon sparkles.
```


### lucky_match.png (new)

```text
Subject: An old brass matchbox with one single partly charred wooden match resting across it, a small warm golden flame and a discreet lucky green enamel detail. An elegant small keepsake that restores a chance.
```


### deep_pockets.png (new)

```text
Subject: A richly shaded worn walnut-brown leather satchel with two distinct generous front pockets, brass clasps and a folded ivory lining visible at the rim. Compact and tactile, no floating loot.
```


### pocket_gavel.png (new)

```text
Subject: A miniature polished dark-walnut judge's gavel with a narrow brass band, resting diagonally over its small matching sound block. Thoughtfully shaped wood and warm restrained highlights.
```


### listening_glass.png (new)

```text
Subject: An antique cut-crystal listening glass tilted on its side, finely shaded smoky blue glass with a silver rim, a single faint teal whisper curling within it. A beautiful small divining object, not a speech-bubble pictogram.
```


### stay_of_execution.png (new)

```text
Subject: A folded ivory court writ held by a deep blue wax seal with a small silver shield-shaped clasp, a short restrained dark-blue ribbon. Distinct from a royal proclamation, no legible writing.
```


### room_reservation.png (new)

```text
Subject: A charming old brass house-room key with a long shaped bow and a blank muted-plum enamel tag, one small ivory door-shaped inlay on the tag, no numbers or writing.
```


## Ominous replacements after playtest feedback

Generated with the built-in `image_gen` tool using `transparent_background=true`. Each existing sprite was edited separately. The returned PNGs were copied unchanged; originals remain in the generated-image directory. These replacements remove the gendered portrait from Dark Whisper and the cheerful heart motif from Love Potion.

### dark_whisper.png (replacement)

Source: `C:/Users/Josh/.codex/generated_images/01a2048b-9a4a-7631-b641-942c692efb20/exec-b57c7c24-8ab8-4e46-9ee0-7425477e04f5.png`

```text
Use case: precise-object-edit. Edit this Dark Whisper inventory sprite. Preserve the beautifully crafted open tarnished-silver locket, centered composition, midnight-teal palette, detailed deliberate pixel-art material work and transparent background. Remove the woman's portrait completely, including every recognizable face, head, hair silhouette and human profile in the vapor. Make its interior an ominous empty ink-dark recess with a restrained thin curl of pale silver-teal whisper-mist escaping from it. Suggest an unseen presence without depicting a person, skull, eyes, ghost mascot or gender. The result should feel mysterious and beautiful, not cute or cartoony. Maintain a strong compact silhouette at64px, crisp square pixel clusters, no text, surrounding scene or opaque background.
```

### love_potion.png (replacement)

Source: `C:/Users/Josh/.codex/generated_images/01a2048b-9a4a-7631-b641-942c692efb20/exec-d6d3683d-4b32-4aa8-bc67-ad789b723bd2.png`

```text
Use case: precise-object-edit. Refine this Love Potion inventory sprite into a more ominous, less sweet magical object while preserving its beautifully drawn faceted glass vial, coherent silhouette, centered composition and transparent background. Shift bright candy pink glass toward deep ruby-wine and muted plum, with dark crimson liquid, restrained warm light through the facets and aged brass rather than shiny cheerful gold. Remove the obvious heart emblem and replace it with a subtle intertwined ornamental motif in the brass collar. Keep the item elegant, tactile and evocative, with deliberate crisp pixel clusters and readability at64px. This is an instantly targeted magical charm in the game: show only the potion object, with no throwing, spraying, drinking figure or delivery animation. No skull, cute face, cartoon sparkles, text, scene, frame or opaque background.
```
