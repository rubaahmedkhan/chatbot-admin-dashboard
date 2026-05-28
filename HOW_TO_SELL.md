# Naye Client Ko Chatbot Kaise Dein

## Aap Ko Kabhi Code Nahi Badalna — Sirf .env File Badlni Hai

---

## Har Naye Client K Liye 3 Steps

### Step 1 — .env mein sirf 2 cheezein badlo

```
SCHOOL_URL=https://client-ki-website.com   ← unki website ka URL
SCHOOL_NAME=Al-Noor Academy                ← unka school naam
```

### Step 2 — Scraper ek baar chalaao

```
python scraper.py
```

Bas. Bot ne unki poori website pad li. Ab woh automatically roz subah update hogi.

### Step 3 — Client ko yeh ek line do

```html
<script src="https://aapka-server.railway.app/static/js/chatbot.js"></script>
```

Unhe apni website mein kisi bhi page par paste karna hai — bas chatbot aa jayega.

---

## Important Notes

- SCHOOL_URL change karne ke baad ek baar `python scraper.py` zaroor chalayein
- Agar client ki website pe admissions close ho gayi — roz subah bot khud update ho jata hai
- Aap ko KABHI dobara deploy nahi karna — ek server pe sab clients ka kaam hota hai
- Har client ka data alag JSON file mein save ho sakta hai (advanced version mein)

---

## Agar Client Ki Apni Website Nahi Hai

Option 1: WhatsApp chatbot banao (Twilio se)
Option 2: Unke liye bhi ek simple website banao — extra charge karo

---

## Pricing Reminder

| Package       | Price              | Kya Mile Ga                                      |
|---------------|--------------------|--------------------------------------------------|
| Monthly       | Rs. 3,000/month    | Website chatbot, auto data sync, admin panel     |
| Annual        | Rs. 30,000/year    | Same as monthly — 2 mahine free (save Rs. 6,000) |
| Setup Fee     | Rs. 5,000 ek baar  | Onboarding, setup, aur embed karna               |
