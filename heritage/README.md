# heritage

**Project 3.** Genealogy graph, print selection engine, and gallery wall planner.

## Genealogy

The FamilySearch API is closed to general public and personal use — do not plan
around a personal API key. Instead, sync a tree through a FamilySearch-certified
desktop application (RootsMagic, MacFamilyTree), export GEDCOM, and load the graph
here. GEDCOM is an ugly format, but it is universal and it outlives vendors.

Ancestor portraits are harvested by hand. Photography realistically reaches back
three or four generations, so this is dozens of people and an afternoon or two — and
it makes a good project to do with kids. Verify direct lines first: collaborative
world trees accumulate bad merges, and a wrongly attached ancestor is worse than a
missing one.

## Selection engine

Choosing wall photos is a constraint problem more than an aesthetics problem.

Quality ranking uses the photo library's own aesthetic and curation scores plus face
signals; no custom aesthetic model. Optionally a vision model judges the finalists for
the one thing the numeric scores miss — genuine expression versus a posed row of faces.

The constraints are the real logic:

- **Per-child quotas enforced in code.** Every subject appears, at multiple ages. In
  a blended family this is the difference between balance being guaranteed and balance
  being hoped for. It is the most important rule in this module.
- Whole-family shots weighted up.
- Era spread and event diversity, so one well-photographed trip can't colonize the wall.
- **Eligibility rule** — configurable per adult: photos of an adult qualify only when
  a subject is also in the shot.

Output is a shortlist at three to five times the frame count. The final cut is human,
ideally made by the family together — that's half the point of the project.

## The payoff

With the genealogy graph and person tags in one index, the archive can generate
cross-generation pairings automatically: a child at eight beside their
great-great-grandparent at eight, printed as a framed side-by-side. This feature is
the reason the index is designed the way it is.

## Wall planner

Frame count is an **output** of wall capacity, not an input. The engine arranges outer
frame dimensions — molding adds width and mats change the opening, so laying out print
sizes produces a wall that doesn't fit.

Validates DPI per assigned print size and previews crops, flagging any assignment where
a 4:3 phone photo in a 5:4 opening would cut off a head.

Outputs: a layout render on a photo of the actual wall, a frame shopping list, a print
order list, and 1:1 printable hanging templates with nail positions marked.

Split this module into `heritage` + `wall` only if it actually grows. Not preemptively.

## Status

Not yet implemented. The selection engine needs a populated index; the genealogy load
can happen any time.
