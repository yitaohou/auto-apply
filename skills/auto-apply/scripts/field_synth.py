#!/usr/bin/env python3
"""field_synth.py — synthetic labelled form fields for fine-tuning the Laya decision layer.

    field_synth.py [--data ~/job-search] [--out data/laya/rows/synth.jsonl] [--check]

Real shadow rows (data/decide_shadow.jsonl) are few and skewed to contact fields; this file
holds hand-written label phrasings for every (class, key) of laya_client.KEYS — the way the
same question is worded on Workday, Greenhouse, Lever, LinkedIn Easy Apply, iCIMS, Workable,
SmartRecruiters, Taleo — crossed with control types, option lists and page titles, rendered
through laya_client.state_text() so training states look exactly like production states.

Labels come from the phrasing table (user-approved key → source mappings in laya_client.KEYS),
never from the model. `--check` runs every phrasing through decide.py's seed rules and prints
the ones where the rules disagree with the hand label, so the two stay consistent.
"""
import argparse, itertools, json, random, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import laya_client as laya  # noqa: E402

DATA_DEFAULT = Path.home() / "job-search"

# (class, key or None) → phrasings. Each phrasing: label  |  label, type  |  label, type, options  |  label, type, options, help
YN = ["Yes", "No"]
YN_DECL = ["Yes", "No", "Prefer not to say"]
COUNTRIES = ["Canada", "United States", "United Kingdom", "India", "Australia", "Germany", "France"]
PROVINCES = ["Alberta", "British Columbia", "Manitoba", "Ontario", "Quebec", "Saskatchewan"]
DEGREES = ["High School", "Associate's Degree", "Bachelor's Degree", "Master's Degree", "Doctorate", "Other"]
SOURCES = ["LinkedIn", "Indeed", "Glassdoor", "Company website", "Referral", "Job fair", "Other"]
WORKMODE = ["Remote", "Hybrid", "On-site", "No preference"]
YEARS = ["0-1", "1-2", "2-4", "4-6", "6-8", "8+"]
AUTH = ["Yes, I am authorized to work in Canada", "No, I will require sponsorship"]
STATUS = ["Canadian citizen", "Permanent resident", "Work permit holder", "Study permit holder", "Other"]
EMP = ["Employed full-time", "Employed part-time", "Self-employed", "Unemployed", "Student"]
NOTICE = ["Immediately", "2 weeks", "1 month", "2 months", "3 months or more"]

P = {
    ("a_contact", "full_name"): [
        "Full name", "Full Name", "Name", "Your name", "Legal name", "Full legal name", "Complete name", "Applicant name",
        "Name (as it appears on your ID)", "Candidate name", "Full name *", "Your full name",
    ],
    ("a_contact", "first_name"): [
        "First name", "First Name", "Given name", "Given Name(s)", "Legal first name", "First", "First name *", "Prénom",
        "Preferred first name", "Preferred name", "Nickname", "What should we call you?", "First / given name",
    ],
    ("a_contact", "last_name"): [
        "Last name", "Last Name", "Surname", "Family name", "Family Name", "Legal last name", "Last", "Last name *", "Nom de famille",
        "Last / family name", "Surname *",
    ],
    ("a_contact", "email"): [
        "Email", "Email address", "E-mail", "Email Address", "Your email", "Contact email", "Preferred email", "Confirm email",
        "Confirm email address", "Email *", "Personal email", "Courriel", "Email (we'll use this to contact you)",
    ],
    ("a_contact", "phone"): [
        "Phone", "Phone number", "Mobile phone number", "Mobile number", "Cell phone", "Telephone", "Contact number", "Phone *",
        "Primary phone", "Best phone number to reach you", "Mobile", "Daytime phone", "Téléphone", "Phone Number (with area code)",
        ("Phone Device Type", "select", ["Mobile", "Home", "Work"]), ("Phone device type", "select", ["Mobile", "Landline"]),
        "Country phone code", ("Country Phone Code", "select", ["+1 Canada", "+1 United States", "+44 United Kingdom"]), "Phone extension", "Ext.",
    ],
    ("a_contact", "linkedin"): [
        "LinkedIn", "LinkedIn Profile", "LinkedIn profile URL", "LinkedIn URL", "Link to your LinkedIn", "LinkedIn profile (optional)",
        "LinkedIn Profile *", "Your LinkedIn", "LinkedIn page", "Profil LinkedIn",
    ],
    ("a_contact", "website"): [
        "Website", "Portfolio", "GitHub", "GitHub profile", "GitHub URL", "Personal website", "Portfolio URL", "Website URL",
        "Portfolio / personal site", "Link to your work (GitHub, portfolio)", "Other website", "Personal site or blog",
    ],
    ("a_contact", "street"): [
        "Address", "Street address", "Address line 1", "Address Line 1", "Street", "Home address", "Mailing address", "Street address line 1",
        "Address 1", "Adresse",
    ],
    ("a_contact", "address2"): [
        "Address line 2", "Address Line 2", "Apartment, suite, unit", "Apt / Suite", "Unit number", "Address 2", "Apartment number",
        "Suite", "Address line 2 (optional)",
    ],
    ("a_contact", "city"): ["City", "City/Town", "Town", "City of residence", "City *", "Ville", "Which city do you live in?", "Current city"],
    ("a_contact", "province"): [
        ("Province", "select", PROVINCES), ("State/Province", "select", PROVINCES), "State", "Province / State", "Region", "State or Province",
        ("Province of residence", "select", PROVINCES), "Province *", "Province/territory",
    ],
    ("a_contact", "postal_code"): ["Postal code", "Postal Code", "Zip code", "ZIP", "Zip / Postal code", "Postcode", "Postal code *", "Code postal"],
    ("a_contact", "location"): [
        "Location", "Location (City)", "Current location", "Where are you located?", "Where do you currently live?", "Current location (city, province)",
        "Location *", "Where are you based?", "Your location", "City and country",
    ],
    ("a_contact", "country"): [
        ("Country", "select", COUNTRIES), ("Country of residence", "select", COUNTRIES), "Country/Region", ("Country *", "select", COUNTRIES),
        "Which country do you live in?", "Country you reside in", ("Country / Region", "select", COUNTRIES),
    ],
    ("b_legal", "authorized"): [
        ("Are you legally authorized to work in Canada?", "radio", YN), ("Are you eligible to work in Canada?", "radio", YN),
        ("Do you have the right to work in Canada?", "select", YN), ("Are you legally entitled to work in Canada?", "radio", YN),
        ("Work authorization", "select", AUTH), ("Are you authorized to work in the country for which you are applying?", "radio", YN),
        ("Are you legally permitted to work in Canada?", "select", YN), ("Do you have a valid work permit for Canada?", "radio", YN),
        ("Are you able to work lawfully in Canada?", "radio", YN), ("Legal right to work in Canada", "select", YN),
        ("Are you currently authorized to work in Canada on a full-time basis?", "radio", YN),
    ],
    ("b_legal", "sponsorship"): [
        ("Will you now or in the future require sponsorship for employment visa status?", "radio", YN),
        ("Do you require visa sponsorship?", "radio", YN), ("Will you require sponsorship to work in Canada?", "select", YN),
        ("Do you now or will you in the future need sponsorship?", "radio", YN), ("Sponsorship required?", "select", YN),
        ("Do you need an employer-sponsored work permit?", "radio", YN), ("Will you need immigration sponsorship?", "radio", YN),
    ],
    ("b_legal", "status"): [
        ("Are you a Canadian citizen or permanent resident?", "radio", YN), ("Citizenship / residency status", "select", STATUS),
        ("What is your current work status in Canada?", "select", STATUS), ("Immigration status", "select", STATUS),
        ("Are you a citizen or permanent resident of Canada?", "radio", YN), ("Current status in Canada", "select", STATUS),
        ("Please select your work status", "select", STATUS),
    ],
    ("b_legal", "citizenship"): [
        ("Nationality", "select", COUNTRIES), ("Country of citizenship", "select", COUNTRIES), "Citizenship", ("Citizenship *", "select", COUNTRIES),
        "What is your nationality?", ("Country of nationality", "select", COUNTRIES),
    ],
    ("b_legal", "background_check"): [
        ("Are you willing to undergo a background check?", "radio", YN), ("Do you consent to a background check?", "radio", YN),
        ("I consent to a background check as a condition of employment", "checkbox"), ("Are you willing to complete a criminal background check?", "radio", YN),
        ("Background check consent", "select", YN), ("Are you willing to submit to a background verification?", "radio", YN),
    ],
    ("b_legal", "criminal"): [
        ("Have you ever been convicted of a criminal offence?", "radio", YN), ("Do you have a criminal record?", "radio", YN),
        ("Have you ever been convicted of a crime for which a pardon has not been granted?", "radio", YN), ("Criminal conviction?", "select", YN),
        ("Have you been convicted of a felony?", "radio", YN),
    ],
    ("b_legal", "licence"): [
        ("Do you have a valid driver's licence?", "radio", YN), ("Valid driver's license?", "select", YN), ("Do you hold a valid driver's license?", "radio", YN),
        ("Driver's licence", "select", YN), ("Do you have a valid G class licence?", "radio", YN),
    ],
    ("b_legal", "age"): [
        ("Are you 18 years of age or older?", "radio", YN), ("Are you at least 18 years old?", "radio", YN), ("Are you over 18?", "select", YN),
        ("Are you of legal working age?", "radio", YN), ("I confirm I am 18 years or older", "checkbox"), ("Age 18 or older?", "select", YN),
    ],
    ("b_legal", "conflict"): [
        ("Do you have any relatives working at this company?", "radio", YN), ("Are you related to any current employee?", "radio", YN),
        ("Do you have a conflict of interest to declare?", "radio", YN), ("Are you bound by a non-compete agreement?", "radio", YN),
        ("Are you subject to a non-disclosure or non-compete agreement with a previous employer?", "radio", YN),
        ("Do you have any outside business interests or directorships?", "radio", YN), ("Is anyone in your household employed here?", "radio", YN),
        ("Do you have a personal or business relationship with any of our employees?", "radio", YN),
        ("Are you subject to any post-employment restrictions?", "radio", YN), ("Do you have a family member employed by the company?", "select", YN),
        ("Are you currently engaged in any secondary employment?", "radio", YN),
    ],
    ("b_legal", "clearance"): [
        ("Do you hold a security clearance?", "radio", YN), ("Current security clearance level", "select", ["None", "Reliability", "Secret", "Top Secret"]),
        ("Are you able to obtain a Government of Canada security clearance?", "radio", YN), ("Do you have an active Secret clearance?", "radio", YN),
        ("Security clearance", "select", ["None", "Enhanced Reliability", "Secret"]),
    ],
    ("c_education", "school"): [
        "School", "University", "Institution", "School name", "Name of institution", "College / University", "School or university", "University name",
        "Institution name", "School *", "Educational institution", "Where did you study?",
    ],
    ("c_education", "degree"): [
        ("Degree", "select", DEGREES), ("Highest level of education", "select", DEGREES), ("Degree type", "select", DEGREES), "Degree",
        ("Education level", "select", DEGREES), ("Highest degree obtained", "select", DEGREES), ("Level of education", "select", DEGREES),
        ("What is the highest level of education you have completed?", "select", DEGREES), "Qualification", ("Highest qualification", "select", DEGREES),
    ],
    ("c_education", "field"): [
        "Field of study", "Major", "Discipline", "Area of study", "Program of study", "Field of Study", "Major / concentration", "Programme", "Specialization",
        "What did you study?",
    ],
    ("c_education", "gpa"): ["GPA", "Grade point average", "GPA (if applicable)", "Cumulative GPA", "GPA *", "Grade / GPA", "Final grade"],
    ("c_education", "edu_start"): [("Education start date", "date"), ("Start date", "date", None, "Education"), ("From (education)", "date"), ("Start year", "select", [str(y) for y in range(2012, 2026)]), ("Year started", "text")],
    ("c_education", "edu_end"): [
        ("Graduation date", "date"), ("Graduation year", "select", [str(y) for y in range(2015, 2027)]), ("End date", "date", None, "Education"),
        ("Expected graduation date", "date"), ("Year of graduation", "text"), ("Date of graduation (or expected)", "date"), ("Degree completion date", "date"),
    ],
    ("d_work", "employer"): [
        "Company", "Employer", "Current company", "Current employer", "Most recent employer", "Company name", "Employer name", "Current / most recent company",
        "Organization", "Where do you currently work?", "Name of employer", "Current Company *",
    ],
    ("d_work", "title"): [
        "Title", "Job title", "Current title", "Current job title", "Most recent title", "Current role", "Position", "Current position", "Your current role",
        "Job Title *", "Role / title", "Most recent job title",
    ],
    ("d_work", "work_start"): [("Employment start date", "date"), ("Start date", "date", None, "Work experience"), ("From", "date", None, "Employment"), ("Date started", "date"), ("Start month/year", "text")],
    ("d_work", "work_end"): [("Employment end date", "date"), ("End date", "date", None, "Work experience"), ("To", "date", None, "Employment"), ("Date ended (leave blank if current)", "date"), ("End month/year", "text")],
    ("d_work", "work_location"): ["Employer location", "Company location", "Location of employer", "Job location", "Where was this role based?", "Office location"],
    ("d_work", "duties"): [("Job description", "textarea"), ("Responsibilities", "textarea"), ("Duties", "textarea"), ("Role description", "textarea"), ("Key responsibilities", "textarea")],
    ("d_work", "total_years"): [
        "Total years of experience", ("Years of professional experience", "select", YEARS), "How many years of work experience do you have?",
        ("Total years of relevant experience", "select", YEARS), "Years of experience (total)", ("Overall years of software development experience", "select", YEARS),
        "How many years of professional experience do you have in total?",
    ],
    ("e_skill_years", None): [
        "Years of experience with React", "How many years of experience do you have with Python?", ("Years of experience with TypeScript", "select", YEARS),
        "Years of AWS experience", "How many years of Java experience do you have?", ("Years of experience: Node.js", "select", YEARS),
        "Years of experience with Angular", "Years of hands-on experience with SQL", "Django experience (years)", "How many years have you worked with Kubernetes?",
        ("Years of experience with Go", "select", YEARS), "Years of experience building REST APIs", "Years of experience with LLM / GenAI applications",
        "Experience with Docker (years)", "Yrs of C# experience", "How many years of experience with cloud platforms (AWS/GCP/Azure)?",
        ("Years of experience with Vue.js", "select", YEARS), "Years of commercial experience with JavaScript", "Years of experience in machine learning",
        "Number of years using PostgreSQL",
    ],
    ("f_preference", "start_date"): [
        ("Available start date", "date"), ("Earliest start date", "date"), "Availability", "When can you start?", ("Start date availability", "date"),
        "Earliest date you are available to start", "When could you start?", ("Available to start", "date"), "Availability to start", "Date available",
    ],
    ("f_preference", "notice"): [("Notice period", "select", NOTICE), "Notice period", "What is your notice period?", ("Required notice period", "select", NOTICE), "Notice period with current employer", "How much notice do you need to give?"],
    ("f_preference", "salary"): [
        "Salary expectations", "Expected salary", "Desired salary", "Compensation expectations", "What are your salary expectations?", "Expected annual salary (CAD)",
        "Salary expectation", "Desired compensation", "Expected pay rate", "Hourly rate expectation", "What is your expected base salary?", "Target compensation",
        ("Expected salary range", "select", ["$60k-$80k", "$80k-$100k", "$100k-$120k", "$120k+"]),
    ],
    ("f_preference", "relocate"): [("Are you willing to relocate?", "radio", YN), ("Open to relocation?", "select", YN), ("Would you relocate for this role?", "radio", YN), ("Willing to relocate", "select", YN), ("Are you willing to relocate to Toronto?", "radio", YN)],
    ("f_preference", "work_mode"): [
        ("Preferred work arrangement", "select", WORKMODE), ("Are you comfortable working on-site?", "radio", YN), ("Are you willing to work in a hybrid model (3 days in office)?", "radio", YN),
        ("Are you open to returning to the office full-time?", "radio", YN), ("Work preference", "select", WORKMODE), ("Are you able to work from our Toronto office?", "radio", YN),
        ("Remote / hybrid / on-site preference", "radio", WORKMODE), ("This role is in-office 4 days a week. Are you comfortable with that?", "radio", YN),
        ("Are you willing to work hybrid?", "select", YN),
    ],
    ("f_preference", "source"): [
        ("How did you hear about us?", "select", SOURCES), ("How did you hear about this position?", "select", SOURCES), "Source", ("Referral source", "select", SOURCES),
        "Where did you find this job?", ("How did you learn about this opportunity?", "select", SOURCES), "Referred by", ("How Did You Hear About Us?", "select", SOURCES),
        "Were you referred by an employee? If so, who?", "Where did you hear about this role?",
    ],
    ("f_preference", "employment_status"): [
        ("Are you currently employed?", "radio", YN), ("Employment status", "select", EMP), ("Current employment status", "select", EMP), ("Are you currently working?", "radio", YN),
        ("Current employment situation", "select", EMP),
    ],
    ("f_preference", "prior_employee"): [
        ("Have you previously worked for this company?", "radio", YN), ("Are you a former employee?", "radio", YN), ("Have you ever been employed by us before?", "radio", YN),
        ("Have you worked at our company before?", "select", YN), ("Previously employed here?", "radio", YN), ("Have you ever applied to or worked for us before?", "radio", YN),
    ],
    ("g_eeo", None): [
        ("Gender", "select", ["Male", "Female", "Non-binary", "Prefer not to say"]), ("Race / ethnicity", "select", ["Asian", "Black", "White", "Prefer not to say"]),
        ("Veteran status", "select", ["I am not a veteran", "I am a veteran", "Prefer not to say"]), ("Disability status", "select", ["Yes", "No", "Prefer not to say"]),
        ("Do you identify as a member of a visible minority?", "radio", YN_DECL), ("Do you identify as Indigenous (First Nations, Métis, Inuit)?", "radio", YN_DECL),
        ("Sexual orientation", "select", ["Heterosexual", "Gay or lesbian", "Bisexual", "Prefer not to say"]), ("Pronouns", "select", ["He/him", "She/her", "They/them", "Prefer not to say"]),
        ("Do you identify as a person with a disability?", "radio", YN_DECL), ("Are you Hispanic or Latino?", "radio", YN_DECL), ("Ethnicity", "select", ["Asian", "Black", "Indigenous", "White", "Prefer not to say"]),
        ("Gender identity", "select", ["Man", "Woman", "Non-binary", "Prefer not to answer"]), ("Voluntary self-identification: gender", "select", ["Male", "Female", "Decline to self-identify"]),
        ("Do you identify as transgender?", "radio", YN_DECL), ("Are you a member of the LGBTQ2S+ community?", "radio", YN_DECL),
    ],
    ("h_free_text", None): [
        ("Why do you want to work here?", "textarea"), ("Why are you interested in this role?", "textarea"), ("Cover letter", "textarea"), ("Tell us about yourself", "textarea"),
        ("Describe a challenging project you worked on", "textarea"), ("What excites you about this opportunity?", "textarea"), ("Why Acme?", "textarea"),
        ("Anything else you'd like us to know?", "textarea"), ("Additional information", "textarea"), ("Describe your experience with distributed systems", "textarea"),
        ("What makes you a good fit for this position?", "textarea"), ("Tell us about a time you disagreed with a teammate", "textarea"),
        ("Describe your involvement in open source", "textarea"), ("Message to the hiring manager", "textarea"), ("Summary", "textarea"),
        ("What is your proudest technical achievement?", "textarea"), ("Comments", "textarea"), ("Explain why you are leaving your current role", "textarea"),
        ("Describe your experience with React and TypeScript", "textarea"), ("How would you approach scaling a web application?", "textarea"),
        ("During this application process, what can we do to make you feel supported?", "textarea"),
    ],
    ("i_compliance", "consent"): [
        ("I certify that the information provided is accurate", "checkbox"), ("I agree to the terms and conditions", "checkbox"), ("I have read and understand the privacy policy", "checkbox"),
        ("I acknowledge the privacy notice", "checkbox"), ("I consent to the processing of my personal data", "checkbox"), ("Do you agree to our privacy policy?", "radio", YN),
        ("I attest that the above information is true and complete", "checkbox"), ("I understand that false statements may result in dismissal", "checkbox"),
        ("Accept privacy statement", "checkbox"), ("By checking this box I confirm I have read the candidate privacy notice", "checkbox"), ("I declare that the information is correct", "checkbox"),
        ("Do you consent to us storing your data for future opportunities?", "radio", YN), ("Terms of use", "checkbox"),
    ],
    ("i_compliance", "signature"): ["Signature", "Electronic signature", "E-signature", "Type your full name as signature", "Sign here (type your name)", "Applicant signature", "Please type your full name to sign", "Digital signature"],
    ("i_compliance", "date"): [("Date", "date"), ("Today's date", "date"), ("Signature date", "date"), ("Date signed", "date"), ("Date (today)", "date"), ("Date of application", "date")],
    ("i_compliance", "marketing"): [
        ("I would like to receive marketing emails", "checkbox"), ("Join our talent community", "checkbox"), ("Sign up for job alerts", "checkbox"), ("I agree to receive SMS/text messages", "checkbox"),
        ("Subscribe to our newsletter", "checkbox"), ("Keep me informed about future opportunities", "checkbox"), ("Opt in to promotional communications", "checkbox"),
        ("Would you like to join our talent network?", "radio", YN), ("Receive text message updates about my application", "checkbox"),
    ],
    ("j_other", None): [
        "Search", "搜尋", "Department", "部門", "Language", ("Preferred language", "select", ["English", "Français"]), "Requisition ID", "Job ID", "Password", "Confirm password", "Username",
        "Captcha", "Security code", "Promo code", "Employee ID", "Candidate number", "Upload transcript", "Reference 1 name", "Reference email", "Emergency contact name",
        "Shirt size", ("T-shirt size", "select", ["S", "M", "L", "XL"]), "Time zone", "Date of birth", "Social insurance number", "Passport number",
    ],
}

ATS = ["greenhouse", "workday", "lever", "linkedin", "icims", "workable", "smartrecruiters", "taleo", "generic"]
PAGES = [
    {"title": "Job Application for Software Engineer at Northwind"}, {"title": "Apply — Full Stack Developer | Contoso"}, {"title": "My Information", "step": "My Information"},
    {"title": "My Experience", "step": "My Experience"}, {"title": "Application Questions", "step": "Application Questions"}, {"title": "Apply to Fabrikam — Senior Developer"},
    {"title": "Easy Apply — Software Developer, Toronto"}, {"title": "Voluntary Disclosures", "step": "Voluntary Disclosures"}, {"title": "Careers — Backend Engineer (Remote, Canada)"}, {},
]
KINDS = {"text": ["input", "input", "input"], "textarea": ["textarea"], "select": ["select", "react-select", "workday-prompt", "combobox"], "radio": ["radio", "button-select"],
         "checkbox": ["checkbox"], "date": ["date", "input", "workday-date"]}
DEFAULT_TYPE = {"a_contact": "text", "b_legal": "radio", "c_education": "text", "d_work": "text", "e_skill_years": "text", "f_preference": "text", "g_eeo": "select",
                "h_free_text": "textarea", "i_compliance": "checkbox", "j_other": "text"}


def expand(entry, cat):
    if isinstance(entry, str):
        entry = (entry,)
    label = entry[0]
    ftype = entry[1] if len(entry) > 1 and entry[1] else DEFAULT_TYPE[cat]
    opts = entry[2] if len(entry) > 2 else None
    help_ = entry[3] if len(entry) > 3 else None
    if ftype in ("select", "radio") and not opts:
        opts = YN if ftype == "radio" else None
    return label, ftype, opts, help_


def generate(seed=11, per_phrasing=3):
    """→ list of laya-train rows (Q1 for every phrasing; Q2 where the class has keys)."""
    rng = random.Random(seed)
    rows = []
    n = 0
    for (cat, key), phrasings in P.items():
        for entry in phrasings:
            label, ftype, opts, help_ = expand(entry, cat)
            for _ in range(per_phrasing):
                ats = rng.choice(ATS)
                page = rng.choice(PAGES)
                f = {"id": f"s{n}", "label": label, "type": ftype, "required": rng.random() < 0.7,
                     "control": {"kind": rng.choice(KINDS.get(ftype, ["input"]))}, "options": opts, "help": help_}
                n += 1
                state = laya.state_text(ats, page, f)
                meta = {"synth": True, "cat": cat, "key": key, "label": label, "ats": ats}
                rows.append({"state": state, "questions": {"q1": laya.Q1}, "expected": {"q1": cat}, "meta": meta | {"q": "q1"}})
                if key and cat in laya.KEYS:
                    qid, q = laya.q2_for(cat)
                    rows.append({"state": state, "questions": {qid: q}, "expected": {qid: key}, "meta": meta | {"q": "q2"}})
    # dedup identical (state, question) pairs
    seen, out = set(), []
    for r in rows:
        k = (r["state"], next(iter(r["questions"])))
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def check(data):
    """Run every phrasing through decide.py's seed rules; print disagreements with the hand label."""
    import decide
    ctx = decide.Ctx(data, "", None)
    bad = 0
    for (cat, key), phrasings in P.items():
        for entry in phrasings:
            label, ftype, opts, help_ = expand(entry, cat)
            f = {"id": "x", "label": label, "type": ftype, "required": True, "control": {"kind": "input"}, "options": opts, "help": help_}
            d = decide.decide_field(ctx, "generic", f)
            lab = laya.label_from_decision(d)
            if lab and (lab[0] != cat or (key and lab[1] and lab[1] != key)):
                bad += 1
                print(f"  DISAGREE {cat}/{key}  ← rules say {lab[0]}/{lab[1]}  : {label!r} [{d['source']}]")
    print(f"check: {bad} disagreements")
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(DATA_DEFAULT))
    ap.add_argument("--out", default=None)
    ap.add_argument("--per-phrasing", type=int, default=3)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    if a.check:
        return 1 if check(a.data) else 0
    rows = generate(per_phrasing=a.per_phrasing)
    out = Path(a.out or (Path(a.data).expanduser() / "data" / "laya" / "rows" / "synth.jsonl"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    import collections
    c = collections.Counter((r["meta"]["q"], r["meta"]["cat"]) for r in rows)
    print(f"synth: {len(rows)} rows → {out}")
    for q in ("q1", "q2"):
        print(f"  {q}: " + ", ".join(f"{cat} {n}" for (qq, cat), n in sorted(c.items()) if qq == q))
    return 0


if __name__ == "__main__":
    sys.exit(main())
