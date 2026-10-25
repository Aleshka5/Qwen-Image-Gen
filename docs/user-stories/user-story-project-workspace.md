# User story: a project workspace for image work

**Status:** implemented in this repository.

**Actor:** a household member who already passed the Auth Gateway on `https://image.filenkov.store`.

**Goal:** keep a photo project, mark parts of it, generate variants, and render the chosen ones, without leaving the page and without this service growing a login.

## Story

1. I open the service and see one workspace. The header shows my name when the gateway sent it.
2. I press "+" and either upload one photo or start from scratch. The project is named with the current Europe/Moscow date and time. I can rename it.
3. The left sidebar lists my projects. "New project" brings me home. I can hide either sidebar. On a narrow screen they are overlays.
4. On a photo I draw rectangles, or I use the whole frame. I can place and move parts even when they overlap. Confirm and save refuses the level until they no longer overlap. After that I can move or delete a part, and I cannot resize it or add another part at that level. Delete or Backspace removes the selected rectangle or circle.
5. I go inside a part. Objects there are photos (deep blue) or text (deep red). I can switch the type. Text shows the original crop and cannot be edited yet. Circles exist only here.
6. I generate from the original crop, with the same fields as before (English prompt, negative, steps, CFG, seed, references, and for a scratch project a size). I can ask for 1 to 8 copies. More than one copy uses random seeds.
7. I pick a provider I enabled in Settings. Local Qwen is always there. Wan, OpenRouter, and Higgsfield need a key that the page never shows again, only a hint.
8. I watch status by polling. A local job shows a stage out of 10. An external job shows queued, running, done, or failed.
9. I choose a variant and render. The picture uses the selected generated variants on the original photo. A circle is clipped to a hard mask.
10. I delete a project and confirm. Its tasks are deleted with their files. A result that finishes after the delete is not kept. Opening a past run from the Feed is [user-story-feed-replay.md](user-story-feed-replay.md).

## Out of scope

OCR, a second public vhost, video models, and any login inside this service.
