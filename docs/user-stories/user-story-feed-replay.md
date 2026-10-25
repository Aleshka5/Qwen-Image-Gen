# User story: open a generation again from the Feed

**Status:** implemented in this repository.

**Actor:** a household member who already passed the Auth Gateway on `https://image.filenkov.store`.

**Goal:** scan my generations as photos, and open the same form that created one, with its prompt and every other setting filled in, so I can change them and run again.

## Story

1. The Feed tab in the right sidebar lists my latest 50 tasks, newest first. Each tile shows only the seed, the status, and the result photo.
2. A task that is not done yet has no photo. Its status is the processing stage (`4/10 denoise` for a local job, or the status word for an external job). The seed is still shown.
3. I click a tile. The page opens the form that created that task. A scratch run opens Generate. A part run opens that part on Customisation or Mask fill, matching the task. The fields are filled in and I can change any of them. Generate starts a new run in that same project.
4. Up returns to the view I was on before the click. The right sidebar stays on the Feed tab. On a wide layout it stays open beside the form. On a narrow layout the overlay still closes so the form is visible, and opening the sidebar again shows Feed.
5. If the project or the part is gone, that task is deleted, together with the files that belong to it, and it is not in the Feed.

## What a tile shows

| Task | On the tile |
|---|---|
| `queued`, `running`, `pending` | Processing stage and seed. No photo. |
| `done` | Status, seed, and the result photo. |
| `failed` | Status word and seed. No photo, and no error paragraph. |

The tile does not show the project name, the prompt, the mode, the provider, or the model. The button's visible text is the status and the seed, so that is also its accessible name. The photo has an empty alt.

A task with no result yet is still clickable.

## What the form receives

The form is the existing one. These fields are set from the task, and each one stays editable:

| Field | Value |
|---|---|
| Prompt, negative prompt | The text stored on the task |
| Steps, CFG | The numbers stored on the task |
| Seed | The seed that was actually used |
| Copies | The batch size stored on the task |
| Provider, model | The provider id and the model id |
| Size | Custom width and height. Quality and aspect are not stored, so the form does not present this run as a preset |
| Reference photos | The stored reference images, in their saved order, back in the reference list |
| Mask | For mask fill, the saved mask painted onto the crop |

Edit modes have no size control. Width and height are still returned on the task for the record.

If that provider is no longer enabled, the provider control falls back to the current default and every other field stays filled in.

Submitting the same inputs, including the same explicit seed and the same image bytes, still returns the existing run. That reuse rule does not change.

## Removal

Deleting a project deletes every task of that project. Deleting a part deletes every task of that part and of its children. In both cases the task row is removed. So are:

- the result file and the variant row written for that task
- the task's artifact files (the stored crop, mask, and references), once no remaining task still points at that file

Copies in one batch share those artifact files. A file is removed only after the last task that uses it is gone.

A result that arrives after the project or the part is gone is not kept. The worker deletes the file it just wrote and deletes the task. It does not leave an `invalidated` row.

`GET /api/feed` and `GET /api/tasks/:id` apply the same removal to a task whose project or part is already gone, so an older `invalidated` row disappears instead of showing up.

## Out of scope

A new form, a full-page gallery, login, and changing the 50-task cap.
