# Project Summary — Multi-Tenant School Chatbot

## Kya Bana?
Ek central server se multiple schools ka chatbot manage karne ka system. Har school ko ek line ka embed code milta hai jo woh apni website mein paste karti hain.

---

## Folder Structure

```
D:\AI chatbots for school\
│
├── app.py                        ← Main server (multi-tenant)
├── scraper.py                    ← Website scraper (per-school)
├── .env                          ← API key + Admin password
├── requirements.txt
│
├── data\
│   └── young-scholars\           ← Har school ka apna folder
│       ├── config.json           ← School info + secret API key
│       └── school_data.json      ← School ka data (fees, admissions, etc.)
│
├── templates\
│   ├── admin.html                ← Admin panel (naya)
│   ├── base.html
│   ├── index.html
│   └── ...
│
└── static\
    └── js\
        └── chatbot.js
```

---

## Naye Files / Changes

### 1. `app.py` — Poora refactor
- Pehle: sirf ek school, hardcoded `.env` mein
- Ab: unlimited schools, har school ka apna route

**Naye Routes:**

| Route | Kaam |
|---|---|
| `POST /chat/<school_id>` | Chat endpoint — school ka data use karta hai |
| `GET /widget/<school_id>.js` | Embeddable JS — school website mein paste karo |
| `GET /admin` | Admin login page |
| `POST /admin/login` | Login karo |
| `GET /admin/panel` | Saari schools dekhein |
| `POST /admin/add-school` | Nai school add karo |
| `POST /admin/toggle/<school_id>` | School band/chalu karo |
| `POST /admin/scrape/<school_id>` | Foran data update karo |

### 2. `scraper.py` — Update
- Pehle: `data/school_data.json` (ek file)
- Ab: `data/{school_id}/school_data.json` (har school ki apni file)

### 3. `templates/admin.html` — Naya file
- Login form
- Stats (total, active, inactive schools)
- Nai school add karne ka form
- Saari schools ki table with embed code + actions

### 4. `data/young-scholars/` — Naya folder
- `config.json` — school info aur secret API key
- `school_data.json` — pehle wala school data migrate kiya

### 5. `.env` — 2 cheezein badli
```
ADMIN_PASSWORD=admin123      ← apna password yahan rakhein
SERVER_URL=http://localhost:5000  ← deploy karne par domain yahan
```

---

## Kaise Kaam Karta Hai

### Nai School Add Karna
1. `http://yourserver.com/admin` kholein
2. Login karein
3. Form mein naam, website URL, phone bharein
4. "School Add Karein" dabayein
5. Embed code copy karein

### School Ko Embed Code Dena
```html
<script src="https://yourserver.com/widget/school-id.js"></script>
```
School wale apni website ke `</body>` se pehle yeh ek line paste karein.
Chatbot khud aa jayega — naam, design, sab kuch automatic.

### Data Auto-Update
- School apni website update kare
- Scraper roz subah 6 AM chale (jab server deployed ho)
- Ya admin panel mein "Data Update" dabao — foran update

### Security
- Har school ka **alag secret API key** hota hai
- Galat key se koi data nahi milta (`401 Unauthorized`)
- School inactive karo → chatbot foran band, school wale pay na karein to disable karo

---

## Language Feature
- User Roman Urdu mein baat kare → chatbot Roman Urdu mein jawab de
- User English mein baat kare → chatbot English mein jawab de
- Yeh feature already system prompt mein set hai

---

## .env File (important)
```
OPENAI_API_KEY=sk-proj-...       ← OpenAI ki key (already set hai)
ADMIN_PASSWORD=admin123          ← yeh apna strong password rakhein
SERVER_URL=http://localhost:5000 ← deploy karne par apna domain yahan
```

---

## Server Chalaana
```bash
python app.py
```
Admin panel: `http://localhost:5000/admin`

---

## Business Model
- **Setup fee:** Rs. 5,000 (ek baar)
- **Monthly:** Rs. 3,000/month
- **Annual:** Rs. 30,000/year (2 mahine free — save Rs. 6,000)
- **Aapka cost:** ~Rs. 500–1,000/month (OpenAI API)
- **Aapka profit:** ~Rs. 2,000–2,500 per client per month

Agar client pay na kare → Admin panel mein "Deactivate" dabao → chatbot band.
