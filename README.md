# ☁️ MyCloud — Private Storage (VPS ke liye)

Apni **private cloud storage** — sirf aap, aapki files, aapka password.
VPS pe host karo, browser se kahin se bhi access karo. **Docker nahi chahiye** — sirf Python 3.

```
┌─────────────────────────────────────────────┐
│  🌐 Browser  →  http://YOUR_VPS_IP:1919     │
│  🔒 Password daalo  →  apni files dekho     │
│  📤 Upload • ⬇️ Download • 🗑 Delete         │
│  📁 Folders • 🔍 Search • 🖼 Preview        │
└─────────────────────────────────────────────┘
```

---

## ⚡ 1 Command me Install (VPS pe)

```bash
curl -fsSL https://raw.githubusercontent.com/siddhantpashte123-hub/cloud/main/install.sh | bash
```

Ya repo clone karke:

```bash
git clone https://github.com/siddhantpashte123-hub/cloud.git
cd cloud
bash install.sh
```

Install ke time ek **password** puchega — wahi aapka login hoga. Bas, ho gaya! 🎉

Phir browser kholo: **`http://YOUR_VPS_IP:1919`**

---

## 🎛 Menu — 1 Install, 2 Uninstall, 3 Status

```bash
./cloud
```

```
========================================
   ☁️  MyCloud — Private Storage
   Sirf aapke liye • Port 1919 • No Docker
========================================
   1) Install      — pehli baar setup
   2) Uninstall    — sab hata do
   3) Status       — chal raha hai ya nahi
   4) Start        — server chalu karo
   5) Stop         — server band karo
   6) Restart      — band karke chalu karo
   7) Password     — password badlo
   8) Logs         — server log dekho
   0) Exit
========================================
```

Direct commands bhi chalenge:

```bash
./cloud install      # setup
./cloud status       # chal raha hai ya nahi
./cloud start        # chalu karo
./cloud stop         # band karo
./cloud restart      # restart
./cloud password     # password badlo
./cloud logs         # log dekho
./cloud uninstall    # sab hata do (files rakhne ka option dega)
```

---

## ✨ Features

| Feature | Detail |
|---|---|
| 🔒 **Password protected** | Enter karte hi password puchega. Galat password = access nahi. |
| 🔐 **Safe hashing** | Password plain text me nahi rakha jata — PBKDF2 hashed (600 perms config). |
| 🛡 **Brute-force protection** | 10 galat try ke baad 60 sec lockout. |
| 📤 **Upload** | Drag & drop ya button — multiple files ek saath. |
| ⬇️ **Download** | Single file ya poora folder ZIP me. |
| 🖼 **Preview** | Photo, video, audio, PDF, text — browser me hi dekho. |
| 📁 **Folders** | Naya folder banao, andar ghuso, rename, delete. |
| 🔍 **Search + Filters** | Photos / Videos / Music / Documents alag se dekho. |
| 💾 **Storage meter** | Kitna space use hua, sidebar me dikhega. |
| 🐳 **No Docker** | Sirf Python 3 stdlib — koi pip package nahi, koi dependency nahi. |
| 🔁 **Auto-start** | systemd wale VPS pe boot pe khud start hota hai. |

---

## 🖥 VPS pe kaise chalega

1. Upar wala **1 command** chalao.
2. Password banao.
3. Browser me `http://YOUR_VPS_IP:1919` kholo → login karo → files upload karo.

**Firewall:** Agar files nahi khul rahi to port 1919 open karo:

```bash
# Ubuntu/Debian (ufw)
sudo ufw allow 1919/tcp

# CentOS/RHEL (firewalld)
sudo firewall-cmd --permanent --add-port=1919/tcp && sudo firewall-cmd --reload
```

**Cloud provider (AWS/GCP/Azure):** Security Group / Firewall rules me port **1919** open karna padega.

---

## 📂 Files kahan save hoti hain?

| Setup | Location |
|---|---|
| Root user (VPS) | `/opt/mycloud/data` |
| Normal user | `~/.mycloud/data` |

Config (password hash): `/opt/mycloud/config.json` (sirf owner padh sakta hai).

Uninstall karte time poochega — files **rakhna hai ya delete** karna hai. Default: files safe rehti hain.

---

## ⚙️ Manual run (advanced)

```bash
python3 server.py --port 1919 --data ./data --config ./config.json
python3 server.py --set-password   # password set/change
```

Environment variables: `MYCLOUD_PORT`, `MYCLOUD_DATA`, `MYCLOUD_CONFIG`, `MYCLOUD_DIR`.

---

## ❓ Troubleshooting

| Problem | Solution |
|---|---|
| `cloud status` me "BAND hai" | `./cloud start` chalao |
| Browser me page nahi khul raha | Port 1919 firewall me open hai? `cloud status` se check karo |
| Password bhool gaya | `./cloud password` se naya banao |
| Server crash ho gaya | `./cloud logs` se error dekho, phir `./cloud restart` |
| Port already in use | `./cloud stop` karke dobara start karo |

---

## 🔒 Security notes

- Password kabhi plain text me save nahi hota (PBKDF2-HMAC-SHA256, 200k rounds).
- Session cookie `HttpOnly` + `SameSite=Lax`, 12 ghante valid.
- Path traversal (`../`) attack block hai — files sirf data folder ke andar.
- **HTTPS ke liye:** Caddy/Nginx reverse proxy lagao (optional, recommended).

---

Made with ❤️ — sirf aapke liye. Apni files, apni marzi. ☁️
