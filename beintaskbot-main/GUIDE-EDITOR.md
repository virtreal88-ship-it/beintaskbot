# Təlimat redaktoru

CRM-də Təlimatlar səhifəsi və /guide-editor ünvanı. Redaktor üçün mövcud approvals_news icazəsi tələb olunur. Məqalələr ilkin sayt fayllarından idxal olunub; redaktor hər açılışda saytdan məlumat köçürmür.

Qaralama düyməsi yalnız qaralamanı saxlayır. Yayımla məqalənin açıq versiyasını saxlayır. Mətn və şəkillər mövcud gh_storage vasitəsilə PostgreSQL və ya GitHub data branch-də daimi saxlanılır. Saytın məqaləni yükləməsi həmin nüsxəni silmir. Versiya konflikti başqa istifadəçinin düzəlişini əvəz etmir.

support-redesign-upload.zip bir dəfə support.akul.az kökünə yüklənir; mövcud static və images faylları saxlanılmalıdır. Botun yenilənmiş main versiyası işə salınmalıdır. Bundan sonra hər redaktədən sonra ZIP yükləmək lazım deyil. Redaktorda ZIP yükləmə düyməsi yoxdur.

/api/guides/public yalnız yayımlanmış mətnləri qaytarır. /api/guides/public/images/... yalnız yayımlanmış şəkilləri göstərir. Qaralamalar və daxili istifadəçi məlumatları açıq API-yə daxil deyil. Redaktə API-si autentifikasiya və icazə tələb edir. Açıq oxuma API-si CORS dəstəkləyir.

Sayt yalnız səhifə açıldıqda və ya yenidən yükləndikdə yenilikləri yoxlayır. Taymer və görünürlük yoxlaması yoxdur. Son uğurlu mətnlər brauzerin localStorage yaddaşında saxlanılır. Şəkillərin əsli botun yaddaşındadır. API əlçatan olmadıqda ilkin təlimatlar və mövcud mətn nüsxəsi qalır; şəkillərin yüklənməsi API-nin əlçatanlığından asılıdır.

Redaktə və yeni məqalə ayrıca modal pəncərədə açılır. Maksimum 5 şəkil: JPEG, PNG, WEBP və AVIF brauzerdə JPEG kimi optimallaşdırılır. Hər optimallaşdırılmış şəkil maksimum 120 KB-dir. SVG və başqa aktiv formatlar qəbul edilmir. Yeni məqalənin bölməsi və ünvanı cari bölməyə görə avtomatik hazırlanır.

Mətn redaktoru: H2, qalın və kursiv, siyahılar, keçid, cədvəl və YouTube. Yeni versiyalar body_format=html ilə saxlanılır; ilkin mətnlər text kimi qalır. Açıq saytda yalnız icazəli HTML elementləri göstərilir; video ünvanları youtube-nocookie.com ilə məhdudlaşdırılır.
