import os
import re
import time
import imaplib
import smtplib
import email
from email.header import decode_header
from email.message import EmailMessage
import requests
import winrm
from dotenv import load_dotenv
from swarmchat.client import Client

load_dotenv()

# --- YAPILANDIRMA ---
SWARM_URL = os.getenv("SWARM_URL")
KANAL = os.getenv("SWARM_CHANNEL", "#helpdesk")

client_agent = Client(SWARM_URL, os.getenv("TOKEN_CLIENT"))
triyaj_agent = Client(SWARM_URL, os.getenv("TOKEN_TRIYJ"))
erisim_agent = Client(SWARM_URL, os.getenv("TOKEN_ERISIM"))
donanim_agent = Client(SWARM_URL, os.getenv("TOKEN_DONANIM"))

OLLAMA_MAIN_URL = os.getenv("OLLAMA_MAIN_URL")
MODEL_MAIN = os.getenv("MODEL_MAIN")
OLLAMA_HERMES_URL = os.getenv("OLLAMA_HERMES_URL")
MODEL_HERMES = os.getenv("MODEL_HERMES")

DC_IP = os.getenv("DC_IP")
DC_USER = os.getenv("DC_USER")
DC_PASS = os.getenv("DC_PASS")

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASS = os.getenv("GMAIL_APP_PASS")

# --- YARDIMCI FONKSİYONLAR ---
def ollama_sor(url, model, sistem_rolu, mesaj, max_token=350):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": sistem_rolu},
            {"role": "user", "content": mesaj}
        ],
        "stream": False,
        "options": {"num_predict": max_token}
    }
    resp = requests.post(url, json=payload, timeout=None)
    resp.raise_for_status()
    return resp.json()["message"]["content"].strip()

KORUMALI_HESAPLAR = {"administrator", "admin", "krbtgt", "guest", "root"}

def ad_guvenli_islem(aksiyon, kullanici_adi, gonderen_mail):
    """Sadece izinli AD komutlarını, Admin koruması ve kimlik doğrulamasıyla çalıştırır."""
    if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", gonderen_mail):
        return f"REDDEDİLDİ: Geçersiz e-posta formatı ({gonderen_mail})."

    # 1. Katman: Statik Kara Liste Kontrolü
    if kullanici_adi.lower() in KORUMALI_HESAPLAR:
        return "REDDEDİLDİ: Ayrıcalıklı (Admin/Sistem) hesaplar üzerinde otomasyon işlemi yapılamaz."

    session = winrm.Session(DC_IP, auth=(DC_USER, DC_PASS), transport="ntlm")
    prefix = "$ProgressPreference = 'SilentlyContinue'; Import-Module ActiveDirectory; "

    # 2. Katman: Dinamik AD AdminCount (Tier-0) ve E-posta Eşleştirme Kontrolü
    # Not: Sadece kendi hesabına işlem yapabilsin (Lab testleri için STRICT_EMAIL_CHECK=false yapılabilir)
    strict_check = os.getenv("STRICT_EMAIL_CHECK", "true").lower() == "true"
    
    guvenlik_kontrolu = (
        f"$u = Get-ADUser -Identity '{kullanici_adi}' -Properties LockedOut, PasswordExpired, AdminCount, EmailAddress -ErrorAction Stop; "
        f"if ($u.AdminCount -eq 1) {{ Write-Output 'REDDEDILDI: Korumali yonetici (Tier-0) hesabi.'; exit 0 }}; "
    )

    if strict_check:
        guvenlik_kontrolu += (
            f"if ($u.EmailAddress -ne '{gonderen_mail}' -and $u.UserPrincipalName -ne '{gonderen_mail}') "
            f"{{ Write-Output 'REDDEDILDI: Talep gonderen e-posta ile hedef AD hesabi eslesmiyor.'; exit 0 }}; "
        )

    if aksiyon == "UNLOCK_USER":
        ps_cmd = (
            f"{prefix}{guvenlik_kontrolu}"
            f"if ($u.LockedOut) {{ Unlock-ADAccount -Identity '{kullanici_adi}'; Write-Output 'BASARILI: {kullanici_adi} hesabinin kilidi acildi.' }} "
            f"else {{ Write-Output 'BILGI: {kullanici_adi} hesabi zaten kilitli degil (Enabled: ' + $u.Enabled + ').' }}"
        )
    elif aksiyon == "CHECK_USER":
        ps_cmd = (
            f"{prefix}{guvenlik_kontrolu}"
            f"$u | Select-Object SamAccountName, Enabled, LockedOut, PasswordExpired | Format-List | Out-String"
        )
    else:
        return "BILGI: Otomatik AD aksiyonu gerekmiyor."

    sonuc = session.run_ps(ps_cmd)
    if sonuc.status_code == 0:
        return sonuc.std_out.decode("utf-8", errors="ignore").strip()
    return f"AD HATASI: {sonuc.std_err.decode('utf-8', errors='ignore').strip()[:300]}"

def eposta_gonder(alici, konu, icerik):
    msg = EmailMessage()
    msg["Subject"] = f"Re: {konu}"
    msg["From"] = GMAIL_USER
    msg["To"] = alici
    msg.set_content(icerik)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_USER, GMAIL_APP_PASS)
        smtp.send_message(msg)

def okunmamis_ticketlari_getir():
    ticketlar = []
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, GMAIL_APP_PASS)
    mail.select("inbox")

    status, mesajlar = mail.search(None, '(UNSEEN SUBJECT "TICKET")')
    for num in mesajlar[0].split():
        _, data = mail.fetch(num, "(RFC822)")
        raw_msg = email.message_from_bytes(data[0][1])

        konu_parca, enc = decode_header(raw_msg["Subject"])[0]
        konu = konu_parca.decode(enc or "utf-8") if isinstance(konu_parca, bytes) else konu_parca
        gonderen = email.utils.parseaddr(raw_msg["From"])[1]

        govde = ""
        if raw_msg.is_multipart():
            for part in raw_msg.walk():
                if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                    govde = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                    break
        else:
            govde = raw_msg.get_payload(decode=True).decode("utf-8", errors="ignore")

        ticketlar.append({"gonderen": gonderen, "konu": konu, "govde": govde.strip()})
    mail.logout()
    return ticketlar

# --- SİSTEM ROLLERİ ---
ROL_TRIYJ = (
    "Sen IT Helpdesk Koordinatörüsün. Gelen talebi analiz et. "
    "Parola, hesap kilidi, Active Directory veya erişim ile ilgiliyse SADECE 'ERISIM' yaz. "
    "Bilgisayar, yazıcı, ağ veya donanım ile ilgiliyse SADECE 'DONANIM' yaz."
)

ROL_ERISIM_HERMES = (
    "Sen Active Directory Erişim Uzmanısın. Gelen talepte geçen kullanıcı adını (örneğin agent1, agent2) bul. "
    "Eğer hesap kilidi açılması isteniyorsa ilk satıra SADECE şu formatı yaz: ACTION: UNLOCK_USER | USER: <kullanici_adi>\n"
    "Eğer hesap durumu soruluyorsa ilk satıra SADECE: ACTION: CHECK_USER | USER: <kullanici_adi>\n"
    "Kullanıcı adı yoksa veya farklı bir erişim talebiyse ilk satıra: ACTION: NONE | USER: none\n"
    "İkinci satırdan itibaren kısa Türkçe teknik notunu yaz."
)

ROL_DONANIM = (
    "Sen IT Donanım ve Sistem Destek Uzmanısın. Gelen arıza talebi için "
    "kısa, net ve adım adım Türkçe çözüm önerisi yaz."
)

ROL_KAPANIS = (
    "Sen IT Helpdesk Koordinatörüsün. Uzmanın teknik çıktısını ve varsa Active Directory işlem sonucunu "
    "son kullanıcıya gönderilecek kibar, net ve kısa bir Türkçe e-posta yanıtına dönüştür."
)

# --- ANA DÖNGÜ ---
print("🤖 Autonomous IT Helpdesk Swarm başlatıldı! Konusunda 'TICKET' geçen mailler dinleniyor...")

while True:
    try:
        yeni_ticketlar = okunmamis_ticketlari_getir()
        for t in yeni_ticketlar:
            tam_metin = f"Konu: {t['konu']}\nTalep: {t['govde']}"
            print(f"\n📩 Yeni Ticket alındı ({t['gonderen']}): {t['konu']}")

            # 1. Client ajanı talebi SwarmChat'e bırakır
            client_agent.send(chat=KANAL, text=f"📩 **YENİ TALEBİM VAR ({t['gonderen']}):**\n{tam_metin}")

            # 2. Triyaj yönlendirir
            kategori = ollama_sor(OLLAMA_MAIN_URL, MODEL_MAIN, ROL_TRIYJ, tam_metin, max_token=20).upper()
            
            if "ERISIM" in kategori:
                triyaj_agent.send(chat=KANAL, text="➡️ Talep `@erisim` (Hermes 3 + Active Directory) uzmanına atandı.")
                hermes_yanit = ollama_sor(OLLAMA_HERMES_URL, MODEL_HERMES, ROL_ERISIM_HERMES, tam_metin)
                
                # Whitelisted AD Aksiyonunu ayrıştır
                ad_sonuc = ""
                eslesme = re.search(r"ACTION:\s*(UNLOCK_USER|CHECK_USER)\s*\|\s*USER:\s*([a-zA-Z0-9._-]+)", hermes_yanit)
                if eslesme:
                    aksiyon, hedef_user = eslesme.group(1), eslesme.group(2)
                    print(f"⚙️ DC üzerinde güvenli işlem çalıştırılıyor: {aksiyon} -> {hedef_user}")
                    ad_sonuc = ad_guvenli_islem(aksiyon, hedef_user, t["gonderen"])  
                
                uzman_ozet = f"🔐 **Erişim Uzmanı (Hermes 3):**\n{hermes_yanit}\n\n🖥️ **DC İşlem Sonucu:**\n{ad_sonuc or 'Aksiyon tetiklenmedi.'}"
                erisim_agent.send(chat=KANAL, text=uzman_ozet)
            else:
                triyaj_agent.send(chat=KANAL, text="➡️ Talep `@donanim` uzmanına atandı.")
                donanim_yanit = ollama_sor(OLLAMA_MAIN_URL, MODEL_MAIN, ROL_DONANIM, tam_metin)
                uzman_ozet = f"🔧 **Donanım Uzmanı:**\n{donanim_yanit}"
                donanim_agent.send(chat=KANAL, text=uzman_ozet)

            # 3. Triyaj kapanış mailini hazırlar ve gönderir
            kapanis_maili = ollama_sor(OLLAMA_MAIN_URL, MODEL_MAIN, ROL_KAPANIS, f"Talep:\n{tam_metin}\n\nÇözüm:\n{uzman_ozet}")
            triyaj_agent.send(chat=KANAL, text=f"✅ **Kullanıcıya Gönderilen Kapanış Maili:**\n{kapanis_maili}")
            
            eposta_gonder(t["gonderen"], t["konu"], kapanis_maili)
            print(f"✅ Ticket çözüldü ve {t['gonderen']} adresine yanıt gönderildi.")

    except Exception as e:
        print(f"⚠️ Döngü hatası: {e}")

    time.sleep(20)
