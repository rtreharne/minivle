# MiniVLE interface system

The MiniVLE interface is mobile-first and intentionally original. It uses a calm academic visual language rather than reproducing another LMS.

## Tokens

- `--navy`, `--blue`: navigation and primary actions.
- `--paper`, `--surface`, `--line`: background, cards, and boundaries.
- `--ink`, `--muted`: primary and secondary text.
- `--mint`, `--amber`: success and needs-attention states.
- `--radius`, `--shadow`: shared surface shape and elevation.

## Components

- `primary-btn`, `secondary-btn`: 44px minimum interactive controls.
- `surface`, `empty-card`, `toast`: information containers and feedback.
- `tag`: compact status indicators.
- `data-table` on desktop becomes purpose-built cards where a data table is not phone-friendly.

## Responsive rules

The base layout supports 320px screens. At 850px, course navigation becomes a horizontally scrollable context switcher and learner navigation becomes a fixed bottom bar. Grade tables become learner cards on phones. Motion is disabled when the user requests reduced motion.
