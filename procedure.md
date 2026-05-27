# School Chatbot - Pura Procedure

---

## Samajhne Wali Baat Pehle

Ek baar deploy karo — phir sab automatic.

```
Aap ek baar server deploy karein
        ↓
Bot khud school ki website roz scan kare
        ↓
Website mein koi bhi change aaye — bot ko khud pata chal jaye
        ↓
Aap ko kuch nahi karna
```

---

## Step 1 — Tools Tayar Karo

Yeh sab ek baar install karna hai laptop mein:

- Python (python.org se download karo)
- VS Code (code editor)
- Git (github.com se)
- Gemini API Key (aistudio.google.com — bilkul free)

---

## Step 2 — Dummy School Website Banao

Pehle ek fake school ki website banao taake demo ho sake.

```
Al-Noor Academy — Demo Website
Pages:
- Home        (school ka naam, photo, welcome message)
- About       (school ke baare mein)
- Admissions  (admission open/closed, process, dates)
- Fee         (class wise fee structure)
- Timings     (school hours, shifts)
- Contact     (address, phone, map)
```

Yeh sirf HTML/CSS mein banegi — koi database nahi chahiye.
Is website ko bhi deploy karna hoga — Railway ya Netlify par (free).

---

## Step 3 — Chatbot Ka Backend Banao

Yeh Python mein hoga. Teen kaam kare ga:

### Kaam 1 — Website Scanner (Scraper)
```
- School ki website ke saare pages read kare
- Saara text nikal le (fee, timings, admission info wagera)
- Ek file mein save kar le
```

### Kaam 2 — Auto Update (Scheduler)
```
- Roz subah 6 baje automatically website dobara scan kare
- Naya data save kar le
- Koi bhi change website mein aaya — bot ko khud pata chal jaye
```

### Kaam 3 — Sawaal Ka Jawab (AI)
```
- User ne sawaal kiya
- Bot ne website ka saved data Gemini AI ko diya
- Gemini ne us data se jawab nikala
- User ko jawab mila
```

---

## Step 4 — Chat Widget Banao

Yeh ek choti si JavaScript file hogi.

```
- Website ke corner mein chat bubble dikhaye
- User click kare toh chat khule
- Sawaal likhe, jawab aaye
- Mobile mein bhi acha dikhe
```

Yeh widget kisi bhi website mein lagane ke liye sirf ek line paste karni hogi:

```html
<script src="https://aapka-server.railway.app/widget.js"></script>
```

---

## Step 5 — Deploy Karo (Sirf Ek Baar)

Railway.app par free mein deploy hoga.

```
Aap ka code GitHub par upload karo
        ↓
Railway se connect karo
        ↓
Deploy ho gaya — link mil jaye ga
        ↓
Bas — ab automatically chalta rahega
```

Server 24/7 chalta rahega.
Roz subah website scan hogi automatically.
Aap ko dobara kuch nahi karna.

---

## Step 6 — Client Ko Kaise Dein

Jab kisi real school ko dena ho:

```
1. Unki website ka URL lo
2. Apne system mein woh URL add karo
3. Bot unki website scan kare ga
4. Unhe sirf ek line code dو
5. Woh apni website mein paste karein
6. Chatbot live ho gaya
```

Naya client = sirf URL add karo.
Zyada deploy nahi karna.

---

## Paise Ka Hisaab

```
Aap ka kharcha (per month):
- Railway server    : Free (ya $5 paid plan)
- Gemini API        : Free (1500 requests/day)
- Domain (optional) : ~$10/year

Aap ki earning (per client per month):
- Basic  (website only)      : 15,000 PKR
- Standard (website+WhatsApp): 25,000 PKR
- Premium (sab kuch)         : 40,000 PKR
```

10 clients = 1,50,000+ per month.

---

## Clients Kaise Milein — Free Pilot Strategy

```
School ko yeh bolein:
"Hum aap ki school ka AI chatbot bilkul free banayenge.
Koi charge nahi. Bas ek choti si video review dein
agar acha lage. Yeh humara demo project hai."

School ne haan kaha
        ↓
Chatbot unki website par lagao
        ↓
Woh khush huay — video review li
        ↓
Us video se dusre clients ko dikhao
        ↓
Paid clients aane lagte hain
```

---

## Poora Flow Ek Nazar Mein

```
[Dummy Website] 
      ↓ scan
[Bot ka Server] ← roz auto scan
      ↓
[Gemini AI] ← user ka sawaal aaya
      ↓
[Chat Widget] ← jawab wapis gaya
      ↓
[School ki Website par dikh raha hai]
```

---

## Agla Qadam

1. Python install karo
2. Gemini API key banao (free)
3. Main dummy school website ka code likhwa dunga
4. Phir chatbot backend
5. Phir widget
6. Phir deploy

Taiyar ho toh batao — shuru karte hain.
