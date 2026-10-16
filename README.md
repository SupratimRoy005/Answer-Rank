# AnswerRank Classroom — teacher and student platform

A responsive classroom web app for teachers to create classes and subjective assignments, and students to join from any internet-connected device, submit answers, see provisional evaluation scores, and receive teacher feedback.

## Features
- Teacher and student account registration/sign-in; password hashes, not plain-text passwords.
- Teachers create classrooms and share a join code.
- Students join classrooms with the code.
- Teachers publish assignments with question, reference answer, and optional rubric points.
- Students submit or update answers.
- Automatic provisional scores for reference similarity, rubric coverage, clarity, and total (0–100, one decimal).
- Teachers review submissions, override the total score, and write feedback.
- Students can view teacher-finalized grade and feedback.
- SQLite for local use; PostgreSQL-ready using `DATABASE_URL` for deployment.
- Access from random devices after deployment to a public host (HTTPS hosting required).

## Run locally (Windows)
1. Install Python 3.10+.
2. Open a terminal in this folder.
3. `python -m venv .venv`
4. `.venv\Scripts\activate`
5. `pip install -r requirements.txt`
6. `python app.py`
7. Visit `http://127.0.0.1:5000`.

For local testing on another device on the same Wi-Fi, run `ipconfig`, find the computer's IPv4 address, then visit `http://YOUR-IP:5000` from that device. Allow Python through the private-network firewall if prompted. This is only local-network access, not public internet hosting.

## Deploy for access from anywhere (Render)
1. Create a GitHub repository and upload all project files from this folder.
2. Sign in to Render and choose **New → Blueprint**.
3. Connect the repository containing `render.yaml`.
4. Deploy. The Blueprint defines a web service and PostgreSQL database, and generates a `SECRET_KEY`.
5. Open the HTTPS URL Render gives you. Register one teacher account and student accounts; share the class join code.
6. Keep the database attached. Do not use SQLite for a hosted ephemeral filesystem.
7. Never commit real secrets. For an existing service, set a strong random `SECRET_KEY` and a private database URL in environment variables.

Hosting provider plans and database availability can change; check the provider's current terms. Deployment is not performed automatically by this code bundle.

## Important security / grading notes
- Anyone can register as a teacher in this starter version. For a real institution, restrict teacher registration with invite codes or admin approval before use.
- Use HTTPS, strong passwords, and backups. Add rate limiting, email verification, CSRF protection, privacy policy, and institution-specific retention rules before production use.
- The automatic grader is a lexical-overlap heuristic, not a trained semantic AI model. It can miss correct answers expressed differently. Scores are explicitly provisional and teachers can override them. Do not use automatic scores as the sole basis for consequential grading.
- No AI API key is included and student answer data is not sent to an external AI service.
