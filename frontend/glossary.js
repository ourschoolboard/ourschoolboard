/* Plain-language definitions for the jargon school budgets are written in.
 *
 * School finance has its own vocabulary, and a parent reading an alert should
 * not have to already know it. Terms get a dotted underline and reveal a
 * definition on click — click rather than hover, because hover does not exist
 * on a phone and this is mostly read on phones.
 *
 * Matching is done over text nodes only, never over markup, and each term is
 * matched at most once per block. Defining the same word four times in one
 * paragraph is noise, and the first occurrence is where a reader stumbles.
 */

export const GLOSSARY = {
  ADA: {
    full: "Average Daily Attendance",
    definition:
      "The average number of students actually present on a school day, not the number enrolled. California funds districts on attendance, so it is the number that drives money. It typically runs a few percent below enrollment."
  },
  TK: {
    full: "Transitional Kindergarten",
    definition:
      "A grade before kindergarten for four-year-olds. California phased it in for every four-year-old by 2025-26 and requires two adults in each classroom, which is why adding it is expensive."
  },
  LCFF: {
    full: "Local Control Funding Formula",
    definition:
      "How California decides what each district should receive per student. The state pays the difference between that target and what local property taxes bring in — unless property taxes already exceed the target."
  },
  LCAP: {
    full: "Local Control and Accountability Plan",
    definition:
      "A district's three-year plan for how it will spend its LCFF money and improve outcomes, updated annually and built with input from parents and the community. State law requires one from every district."
  },
  COLA: {
    full: "Cost-of-Living Adjustment",
    definition:
      "The annual inflation increase the state adds to district funding. Community-funded districts like Mill Valley do not receive it."
  },
  EIR: {
    full: "Environmental Impact Report",
    definition:
      "A study California requires before a major construction project, covering effects like traffic, noise and soil. Certifying it is the step that lets a project proceed."
  },
  FTE: {
    full: "Full-Time Equivalent",
    definition:
      "One full-time position. Two half-time staff count as one FTE, so it measures total staffing rather than headcount."
  },
  MYP: {
    full: "Multi-Year Projection",
    definition:
      "The district's forecast for the current year plus the next two. California requires it so problems show up before they arrive."
  },
  SACS: {
    full: "Standardized Account Code Structure",
    definition:
      "The uniform accounting format every California district must file in, so budgets can be compared between districts."
  },
  RFP: {
    full: "Request for Proposals",
    definition:
      "A competitive process asking vendors to propose how they would do a project and what it would cost. The district evaluates the proposals before choosing a vendor."
  },
  PPA: {
    full: "Power Purchase Agreement",
    definition:
      "A long-term contract to buy electricity from a provider that installs and operates an energy system, often solar panels, rather than the district buying the system itself."
  },
  MOU: {
    full: "Memorandum of Understanding",
    definition:
      "A written agreement describing what two parties have agreed to do. In school districts it often records a temporary or specific agreement with an employee union or partner."
  },
  AP: {
    full: "Advanced Placement",
    definition:
      "College-level high school courses with standardized exams. Qualifying exam scores may earn college credit or advanced placement, depending on the college."
  },
  ELA: {
    full: "English Language Arts",
    definition:
      "The school subject covering reading, writing, speaking, listening and language skills."
  },
  ELD: {
    full: "English Language Development",
    definition:
      "Instruction designed to help students who are learning English develop the language skills needed for school."
  },
  CTE: {
    full: "Career Technical Education",
    definition:
      "Courses that combine academics with practical preparation for an industry or career, such as health care, engineering, agriculture or media production."
  },
  HVAC: {
    full: "Heating, Ventilation and Air Conditioning",
    definition:
      "The building systems that control indoor temperature and air quality. School HVAC projects often involve replacing aging equipment or improving ventilation."
  },
  MSBA: {
    full: "Massachusetts School Building Authority",
    definition:
      "The state agency that helps Massachusetts communities pay for eligible public-school construction costs. Its reimbursement covers only approved costs, so the town must fund the rest."
  },
  CAASPP: {
    full: "California Assessment of Student Performance and Progress",
    definition:
      "California's statewide testing system. Its results show how students are performing against state academic standards."
  },
  SBAC: {
    full: "Smarter Balanced Assessment Consortium",
    definition:
      "The English and math tests used within California's statewide assessment system, including annual tests and optional interim checks during the school year."
  },
  FCMAT: {
    full: "Fiscal Crisis and Management Assistance Team",
    definition:
      "A California public agency that helps districts with financial and management problems and independently evaluates their risk of insolvency."
  },
  RIF: {
    full: "Reduction in Force",
    definition:
      "A formal elimination of jobs, usually because of budget cuts, declining enrollment or program changes. It may result in layoffs or reduced hours."
  },
  LEA: {
    full: "Local Educational Agency",
    definition:
      "The public organization legally responsible for providing education—usually a school district, county office of education or independently governed charter school."
  },
  ELOP: {
    full: "Expanded Learning Opportunities Program",
    definition:
      "California funding for before-school, after-school, summer and intersession programs, especially for younger and higher-need students."
  },
  SELPA: {
    full: "Special Education Local Plan Area",
    definition:
      "A California regional organization through which districts coordinate special-education services, funding and compliance."
  },
  IDEA: {
    full: "Individuals with Disabilities Education Act",
    definition:
      "The federal law guaranteeing eligible students with disabilities a free appropriate public education and requiring schools to provide individualized services."
  },
  CEQA: {
    full: "California Environmental Quality Act",
    definition:
      "The state law requiring public agencies to study and disclose the environmental effects of projects before approving them."
  },
  DTSC: {
    full: "Department of Toxic Substances Control",
    definition:
      "The California agency that oversees investigation and cleanup of hazardous substances, including contaminated soil at school construction sites."
  },
  DSA: {
    full: "Division of the State Architect",
    definition:
      "The California office that reviews public-school construction plans for structural safety, fire and life safety, and accessibility."
  },
  RAW: {
    full: "Removal Action Workplan",
    definition:
      "A detailed cleanup plan describing how contaminated soil or other hazardous material will be safely removed or managed at a site."
  },
  "Title I": {
    full: "Title I",
    definition:
      "Federal funding that helps schools and districts serve students from lower-income families and improve academic outcomes."
  },
  "certificated": {
    full: "Certificated employees",
    definition:
      "School employees whose jobs require a state credential, such as teachers, counselors, principals and some specialists."
  },
  "classified": {
    full: "Classified employees",
    definition:
      "School employees in jobs that do not require a teaching or administrative credential, such as aides, office staff, custodians, drivers and food-service workers."
  },
  "Second Interim": {
    full: "Second Interim Financial Report",
    definition:
      "A required midyear update showing whether a California district expects to meet its financial obligations in the current year and the next two years."
  },
  "positive certification": {
    full: "Positive budget certification",
    definition:
      "The district certifies that it expects to meet its financial obligations this year and for the following two years. It does not necessarily mean the budget is balanced or free of risk."
  },
  "qualified certification": {
    full: "Qualified budget certification",
    definition:
      "A warning that a district may not be able to meet its financial obligations this year or in either of the following two years without corrective action."
  },
  "qualified fiscal certification": {
    full: "Qualified budget certification",
    definition:
      "A warning that a district may not be able to meet its financial obligations this year or in either of the following two years without corrective action."
  },
  "consent calendar": {
    full: "Consent calendar",
    definition:
      "A group of routine board items approved together in one vote, usually without separate discussion unless a board member asks to pull an item out."
  },
  "general obligation bond": {
    full: "General obligation bond",
    definition:
      "Long-term borrowing approved by voters and repaid through local property taxes. School districts generally use the proceeds for buildings and equipment, not operating expenses or salaries."
  },
  "general obligation bonds": {
    full: "General obligation bonds",
    definition:
      "Long-term borrowing approved by voters and repaid through local property taxes. School districts generally use the proceeds for buildings and equipment, not operating expenses or salaries."
  },
  "community funded": {
    full: "Community-funded (also called basic aid)",
    definition:
      "A district where local property taxes already exceed what the state formula says it should get, so the state sends essentially nothing. About 139 of California's roughly 1,000 districts. It is automatic, not chosen — and it means enrollment growth brings no extra money."
  },
  "Criteria and Standards": {
    full: "Criteria and Standards review",
    definition:
      "A standard checklist every California district must run its budget through. Items come back 'Met' or 'Not Met'. It is an early-warning tool, not a pass/fail exam."
  },
  "deficit spending": {
    full: "Deficit spending",
    definition:
      "Spending more in a year than the district takes in, covering the gap from savings. Sustainable briefly; a problem if it repeats."
  },
  "fund balance": {
    full: "Fund balance",
    definition:
      "Money left over from prior years — the district's savings account. A negative fund balance means the money is gone and another fund has to cover it."
  },
  "parcel tax": {
    full: "Parcel tax",
    definition:
      "A flat annual tax on each property, charged the same regardless of the property's value. California districts need a two-thirds vote to pass one."
  },
  "Measure G": {
    full: "Measure G",
    definition:
      "The voter-approved construction bond funding the middle school project. Bond money can only be spent on buildings — never on salaries or programmes."
  },
  "Measure E": {
    full: "Measure E",
    definition:
      "The parcel tax Mill Valley voters approved in June 2026: $1,754 per parcel each year through 2034, rising 5% annually."
  },
  "unrestricted": {
    full: "Unrestricted funds",
    definition:
      "Money the district can spend on anything. The opposite is restricted funds, which arrive tagged for one purpose such as special education."
  },
  "encroachment": {
    full: "Encroachment",
    definition:
      "When a programme costs more than its own dedicated funding, so general money must cover the shortfall. Special education encroaches in nearly every California district."
  },
  "reserve": {
    full: "Reserve",
    definition:
      "Savings held against emergencies and to cover payroll between tax payments, usually quoted as a percentage of yearly spending. The state minimum is 3%; most districts aim far higher."
  }
};

/* Longest first, so "deficit spending" wins over a bare "deficit" and
   "Criteria and Standards" is never chopped in half. */
const TERMS = Object.keys(GLOSSARY).sort((a, b) => b.length - a.length);

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// \b would not fire on a term containing a space, so bound on non-word chars.
const PATTERN = new RegExp(`(?<![\\w-])(${TERMS.map(escapeRegExp).join("|")})(?![\\w-])`, "gi");

function popover(key, entry) {
  const wrap = document.createElement("span");
  wrap.className = "glossary-wrap";

  const button = document.createElement("button");
  button.type = "button";
  button.className = "glossary-term";
  button.textContent = key;
  button.setAttribute("aria-expanded", "false");
  button.setAttribute("aria-label", `${key} — show definition`);

  const panel = document.createElement("span");
  panel.className = "glossary-panel";
  panel.hidden = true;
  const title = document.createElement("strong");
  title.textContent = entry.full;
  const body = document.createElement("span");
  body.textContent = entry.definition;
  panel.append(title, body);

  button.addEventListener("click", (event) => {
    /* preventDefault as well as stopPropagation: on the feed, these buttons sit
     * inside the alert card, which is an <a>. Stopping the bubble keeps the
     * document handler from closing the panel, but the anchor's own default
     * still fires and navigates away — so the definition appeared to do
     * nothing at all. */
    event.preventDefault();
    event.stopPropagation();
    const open = !panel.hidden;
    // Only one definition open at a time; stacked popovers are unreadable.
    document.querySelectorAll(".glossary-panel:not([hidden])").forEach((p) => {
      p.hidden = true;
      p.previousElementSibling?.setAttribute("aria-expanded", "false");
    });
    panel.hidden = open;
    button.setAttribute("aria-expanded", String(!open));
  });

  wrap.append(button, panel);
  return wrap;
}

/** Return a fragment with known terms wrapped, leaving everything else alone. */
export function withGlossary(text) {
  const fragment = document.createDocumentFragment();
  const value = String(text ?? "");
  const seen = new Set();
  let last = 0;

  for (const match of value.matchAll(PATTERN)) {
    const matched = match[0];
    const key = TERMS.find((t) => t.toLowerCase() === matched.toLowerCase());
    if (!key || seen.has(key.toLowerCase())) continue;   // first mention only
    // Acronyms are case-sensitive even though ordinary glossary phrases are
    // not. Otherwise IDEA and RAW would decorate ordinary prose such as
    // "an idea" or "raw data", and ADA could decorate a person's name.
    const uppercaseAcronym = /^[A-Z][A-Z0-9&/-]+$/.test(key);
    if (uppercaseAcronym && matched !== key) continue;
    seen.add(key.toLowerCase());
    fragment.append(document.createTextNode(value.slice(last, match.index)));
    fragment.append(popover(matched, GLOSSARY[key]));
    last = match.index + matched.length;
  }
  fragment.append(document.createTextNode(value.slice(last)));
  return fragment;
}

// Clicking anywhere else closes an open definition.
document.addEventListener("click", () => {
  document.querySelectorAll(".glossary-panel:not([hidden])").forEach((p) => {
    p.hidden = true;
    p.previousElementSibling?.setAttribute("aria-expanded", "false");
  });
});
