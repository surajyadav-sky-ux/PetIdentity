# PetQR — Multi-User Pet QR Code System

A Flask + SQLite web app where each user manages their own pets, and every pet
gets a unique QR code linking to a public profile page (no login required to view).

## Features

- User registration, login, logout (session-based auth, hashed passwords)
- Each user only sees/edits/deletes their own pets (enforced server-side by `user_id`)
- Add pets with photo, breed, age, gender, owner contact info, etc.
- Auto-generated QR code per pet, pointing to `/pet/<unique_id>`
- Public pet profile page works without any login — anyone who scans the QR
  code can see the pet + owner contact details
- Download QR code as PNG (print it and put it on a collar/tag)

## Setup

1. Create a virtual environment (recommended):

   ```bash
   python -m venv venv
   source venv/bin/activate      # Windows: venv\Scripts\activate
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Run the app (this also creates `database.db` automatically on first run):

   ```bash
   python app.py
   ```

4. Open your browser at `http://localhost:5000`

## How the multi-user separation works

- Every pet row stores a `user_id` foreign key.
- `/dashboard` only ever queries `WHERE user_id = ?` for the logged-in user.
- `/pet/edit/<id>` and `/pet/delete/<id>` fetch the pet, check
  `pet.user_id == session['user_id']`, and return `403 Forbidden` if it
  belongs to someone else — so User 1 can never edit/delete User 2's pets,
  even by guessing a pet's numeric ID in the URL.
- The public route `/pet/<unique_id>` uses a separate random 12-character
  `unique_id` (not the database `id`), so QR codes can't be guessed or
  enumerated, and this route has no login check at all.

## Database schema

**users**: `id, name, email, password_hash, created_at`

**pets**: `id, user_id, unique_id, pet_name, pet_type, breed, age, gender,
color, photo, owner_name, phone, email, address, additional_info, qr_code,
created_at`

(`unique_id` was added to the schema you specified — it's the random slug
used in the public QR URL, kept separate from the internal `id` for security.)

## QR codes are plain HTTPS links (universally scannable)

Every QR code encodes nothing but a normal URL, e.g.:

```
https://yourdomain.com/pet/8f72k91
```

There is no app-specific payload, so **any** standard scanner — the iPhone
Camera app, Google Lens, the Samsung camera's built-in scanner, a generic
Android QR app — recognizes it as a link and opens it straight in the
phone's browser. No app install, no account, and no login is required to
view the page at `/pet/<id>`; that route is intentionally public.

**Important for production:** set the `SITE_URL` environment variable to
your real public domain before generating QR codes, e.g.:

```bash
export SITE_URL="https://yourdomain.com"
```

Without this, the app falls back to building the URL from the incoming
request. That's fine once you're running behind your real domain over
HTTPS, but if you generate a QR code while testing locally (e.g. at
`http://localhost:5000`), the code will encode that local address instead
of your public one — so set `SITE_URL` before printing any QR codes you
intend to actually use.

## Address privacy

When adding or editing a pet, the owner can check "Show this address on
the public pet profile." If left unchecked (the default), the address is
stored but never shown to anyone scanning the QR code — only the pet's
name, type, breed, age, color, notes, and owner contact (phone/email) are
public.

## Notes for production use

- Change `app.config["SECRET_KEY"]` to a real secret (use an environment variable).
- Set `SITE_URL` to your real HTTPS domain (see above).
- Turn off `debug=True` in `app.py`.
- Consider adding rate limiting on `/login` and email verification on `/register`.
- Move uploaded photos / QR codes to cloud storage (e.g. S3) if deploying somewhere
  with an ephemeral filesystem (like Heroku or Render's free tier).
