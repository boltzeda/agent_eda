---
name: skill-builder
description: >
  Use this skill whenever a user wants 
  to create, design, or build a new Claude skill — even if they have zero technical knowledge. Triggers include: "I want to build a skill", "help me create a skill", "make a skill for X", "I want Claude to always do X", "can you teach Claude to do Y", "build a workflow skill", "I want to save this as a skill", "create a reusable skill", or any time a user describes a repeatable task they want Claude to handle consistently. Also triggers when a user says things like "I keep doing this task over and over", "I want Claude to always respond this way", or "how do I make Claude do this automatically". This skill walks users through the entire skill creation process — from zero to a ready-to-use .skill file — with plain-language guidance at every step, no coding knowledge required.
---

# Skill Builder

This skill helps anyone — regardless of technical background — create a high-quality Claude skill from scratch. By the end of the process, the user will have a complete, packaged `.skill` file they can install and use immediately.

---

## Philosophy

A skill is simply a set of written instructions that tells Claude how to handle a specific type of task — like a recipe for Claude to follow. Think of it like training a very capable assistant: you describe what you want done, how to do it, and what good results look like. That's all a skill is.

---

## Step 1: Understand What the User Wants

Start by greeting the user warmly and explaining the process in simple terms. Don't use jargon unless the user does first.

Ask these questions — but keep it conversational, not like a form:

1. **What should this skill help Claude do?**  
   Ask them to describe the task in plain language. Examples: "Write professional emails", "Summarize documents", "Turn meeting notes into action items", "Always respond in a specific tone".

2. **When should it kick in?**  
   What kinds of requests or phrases should trigger this skill? Help them brainstorm: "Any time someone says 'write me an email'?" or "Every time I upload a PDF?"

3. **What should the output look like?**  
   A document? A list? A formatted report? A specific length or tone?

4. **Are there any rules or things to always avoid?**  
   For example: "Never use bullet points", "Always include a summary at the top", "Don't be too formal."

5. **Do you have an example of a great output?**  
   If so, ask them to share it — real examples are incredibly helpful.

If the user seems unsure, help them by offering examples:
> "For instance, a skill might be: 'Whenever I share a sales call transcript, always extract the top 3 action items and write a follow-up email.' Does something like that sound right?"

---

## Step 2: Name the Skill

Once you understand the task, suggest a short, descriptive name (lowercase, hyphenated):
- `email-writer`
- `meeting-summarizer`
- `legal-doc-reviewer`

Ask the user if the name feels right, or if they want something different.

---

## Step 3: Write the SKILL.md

Now write the skill file. Structure it like this:

```
---
name: <skill-name>
description: >
  [A clear, specific description of WHEN to use this skill and WHAT it does. 
   Be slightly "pushy" — list multiple trigger phrases so Claude always knows when to use it.
   Example: "Use whenever a user shares a transcript, meeting notes, or recording. 
   Also triggers on: 'summarize this call', 'what were the action items', 'follow up from this meeting'."]
---

# [Skill Name]

[1-2 sentence summary of what this skill does and why it matters]

---

## When To Use This Skill
[Describe the situations where Claude should apply these instructions]

## Step-by-Step Instructions
[Clear, numbered steps for Claude to follow]

## Output Format
[Describe exactly what the output should look like — length, structure, tone]

## Rules & Guardrails
[Any hard rules: things to always do, things to never do]

## Example
[If the user provided an example, include it here as a reference]
```

Show the draft to the user. Explain each section in plain language:
> "The 'description' at the top is like a label — it helps Claude know when to use this skill. The steps below are the actual instructions Claude will follow."

Ask: "Does this capture what you had in mind? What would you change?"

---

## Step 4: Refine Based on Feedback

Make edits based on the user's feedback. Common things to improve:
- The description needs more trigger phrases (so Claude always recognizes when to use the skill)
- The steps are too vague or too detailed
- The output format needs adjusting
- Missing an important rule or guardrail

Iterate until the user is happy. You don't need more than 2-3 rounds typically.

---

## Step 5: Test the Skill (Optional but Recommended)

Offer to test the skill by simulating it yourself:

> "Want me to try using this skill right now on a real example? You can give me a sample input and I'll follow the skill instructions and show you what output it produces."

Run through the skill instructions with a real or made-up input. Show the output and ask:
> "How does that look? Is this what you were expecting?"

If something's off, update the skill and test again.

---

## Step 6: Package and Deliver

Once the user is happy, save the final SKILL.md file and package it.

**Save the skill:**
```bash
mkdir -p /home/claude/<skill-name>
# Write SKILL.md to this directory
```

**Package it:**
```bash
python -m scripts.package_skill /home/claude/<skill-name>
```

If the packaging script isn't available, manually zip the skill folder:
```bash
cd /home/claude && zip -r <skill-name>.skill <skill-name>/
cp <skill-name>.skill /mnt/user-data/outputs/
```

Present the `.skill` file to the user using `present_files`.

Then tell them:
> "Your skill is ready! You can install it in Claude by going to Settings → Skills and uploading the file. After that, Claude will automatically use this skill whenever the right situation comes up."

---

## Communication Style

Match your language to the user:
- **Non-technical user**: Use plain English. Avoid "YAML", "frontmatter", "JSON", "assertions". Say "the label at the top of the file" instead of "YAML frontmatter".
- **Technical user**: You can use correct terminology and go faster.

Signs the user is non-technical: they describe their workflow in everyday language, they ask "what's a skill?", they seem uncertain about what to provide.

Signs the user is technical: they mention file formats, APIs, code, or use terms like "trigger" or "output schema" correctly.

Adjust accordingly. When in doubt, be simpler.

---

## Quick Reference: Anatomy of a Good Skill

| Part | Purpose | Example |
|------|---------|---------|
| `name` | Unique identifier | `meeting-summarizer` |
| `description` | Tells Claude WHEN to use the skill | "Use whenever user shares meeting notes or transcripts..." |
| Instructions body | Tells Claude HOW to do the task | Step-by-step numbered instructions |
| Output format | Tells Claude WHAT to produce | "Always write: Summary → Action Items → Follow-up Email" |
| Rules | Hard constraints | "Never skip the summary. Always use plain language." |
| Examples | Shows what great looks like | A sample output the user shared |

---

## Handoff to Skill Evaluator

After delivering the skill, let the user know they can measure and improve it over time:

> "Now that your skill is built, you can evaluate how well it performs using the **Skill Evaluator**. It scores your skill on clarity, coverage, and output quality — and gives you specific suggestions for improvement. Just say 'evaluate my skill' to get started."

---

## Edge Cases

- **User wants to update an existing skill**: Ask them to share the existing SKILL.md. Read it, understand what they want changed, make the edits, re-test, and re-package.
- **User isn't sure what they want**: Walk them through examples from different domains (writing, summarization, data extraction, formatting, tone/style) until something clicks.
- **User wants a very complex skill**: Break it into phases. Build a simple v1 first, test it, then layer in complexity.
- **User provides an example output**: Use it! Reverse-engineer the instructions from the example. Ask "Is this the kind of output you always want?"