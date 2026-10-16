import os, re, secrets, string
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session, abort
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-this-before-deploying")
db_url = os.environ.get("DATABASE_URL", "sqlite:///answerrank.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
db = SQLAlchemy(app)

STOP = set("""a an the and or but if then than of to in on for from by with is are was were be been being it its this that these those as at into about over under can could should would may might will do does did have has had not no we they you your their our which who what when where why how also very more most some such""".split())

def now_utc():
    return datetime.now(timezone.utc)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # teacher/student
    created_at = db.Column(db.DateTime(timezone=True), default=now_utc, nullable=False)

class Classroom(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    subject = db.Column(db.String(120), nullable=False, default="General")
    join_code = db.Column(db.String(12), unique=True, nullable=False, index=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=now_utc, nullable=False)
    teacher = db.relationship("User", backref="classrooms")
    memberships = db.relationship("Membership", cascade="all, delete-orphan", backref="classroom")
    assignments = db.relationship("Assignment", cascade="all, delete-orphan", backref="classroom")

class Membership(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    classroom_id = db.Column(db.Integer, db.ForeignKey("classroom.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    joined_at = db.Column(db.DateTime(timezone=True), default=now_utc, nullable=False)
    student = db.relationship("User", backref="memberships")
    __table_args__ = (db.UniqueConstraint("classroom_id", "student_id", name="unique_membership"),)

class Assignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    classroom_id = db.Column(db.Integer, db.ForeignKey("classroom.id"), nullable=False)
    title = db.Column(db.String(180), nullable=False)
    question = db.Column(db.Text, nullable=False)
    reference_answer = db.Column(db.Text, nullable=False)
    rubric_text = db.Column(db.Text, nullable=False, default="")
    due_date = db.Column(db.String(30), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=now_utc, nullable=False)
    submissions = db.relationship("Submission", cascade="all, delete-orphan", backref="assignment")

class Submission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey("assignment.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    answer = db.Column(db.Text, nullable=False)
    similarity = db.Column(db.Float, nullable=False, default=0)
    rubric_score = db.Column(db.Float, nullable=False, default=0)
    clarity = db.Column(db.Float, nullable=False, default=0)
    total_score = db.Column(db.Float, nullable=False, default=0)
    feedback = db.Column(db.Text, nullable=False, default="")
    teacher_feedback = db.Column(db.Text, nullable=False, default="")
    graded = db.Column(db.Boolean, nullable=False, default=False)
    submitted_at = db.Column(db.DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)
    student = db.relationship("User", backref="submissions")
    __table_args__ = (db.UniqueConstraint("assignment_id", "student_id", name="unique_submission"),)

def tokens(text):
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 1 and w not in STOP]

def similarity(a, b):
    aa, bb = set(tokens(a)), set(tokens(b))
    return len(aa & bb) / len(aa | bb) if aa and bb else 0.0

def rubric_points(reference, rubric_text):
    if rubric_text.strip():
        points = [p.strip(" •-\t") for p in re.split(r"[\n;]+", rubric_text) if p.strip()]
        if points:
            return points[:20]
    pieces = [p.strip(" \t\r\n-•") for p in re.split(r"(?<=[.!?;])\s+|\n+", reference) if p.strip()]
    pieces = [p for p in pieces if len(tokens(p)) >= 3]
    if len(pieces) < 2:
        words = reference.split()
        pieces = [" ".join(words[i:i+12]).strip(" .;,:") for i in range(0, len(words), 12)]
        pieces = [p for p in pieces if len(tokens(p)) >= 3]
    out = []
    for p in pieces:
        if not any(similarity(p, old) > .82 for old in out):
            out.append(p.rstrip(".!?;"))
    return out[:12] or [reference]

def coverage(answer, point):
    words, required = set(tokens(answer)), tokens(point)
    return sum(1 for word in required if word in words) / len(required) if required else 0.0

def clarity_score(text):
    words = (text or "").split()
    if not words: return 0.0
    sentences = max(1, len(re.findall(r"[.!?]+", text)))
    avg = len(words) / sentences
    ts = tokens(text)
    variety = len(set(ts)) / max(1, len(ts))
    length = min(1.0, len(words) / 45)
    sentence = 1.0 if 5 <= avg <= 28 else max(.35, 1 - abs(avg - 16) / 30)
    return max(0, min(100, (0.45*length + .30*sentence + .25*variety)*100))

def auto_grade(assignment, answer):
    points = rubric_points(assignment.reference_answer, assignment.rubric_text)
    sim = similarity(answer, assignment.reference_answer) * 100
    rubric = sum(coverage(answer, p) for p in points) / max(1, len(points)) * 100
    clarity = clarity_score(answer)
    total = sim * .35 + rubric * .45 + clarity * .20
    missing = [p for p in points if coverage(answer, p) < .45]
    feedback = "Good coverage of the generated rubric points." if not missing else "Review these points: " + "; ".join(missing[:5])
    return round(sim,1), round(rubric,1), round(clarity,1), round(total,1), feedback

def current_user():
    uid = session.get("user_id")
    return db.session.get(User, uid) if uid else None

@app.context_processor
def inject_user():
    return {"current_user": current_user()}

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def role_required(role):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                flash("Please sign in to continue.", "warning")
                return redirect(url_for("login"))
            if user.role != role:
                abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator

def teacher_owns_classroom(classroom):
    if classroom.teacher_id != current_user().id:
        abort(403)

def membership_for(classroom_id, student_id):
    return Membership.query.filter_by(classroom_id=classroom_id, student_id=student_id).first()

@app.route("/")
def index():
    if current_user():
        return redirect(url_for("dashboard"))
    return render_template("index.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name","").strip()
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        role = request.form.get("role","student")
        if not name or not email or len(password) < 8 or role not in ("teacher","student"):
            flash("Enter your name, a valid email, and a password of at least 8 characters.", "danger")
            return render_template("register.html")
        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "danger")
            return render_template("register.html")
        user = User(name=name, email=email, password_hash=generate_password_hash(password), role=role)
        db.session.add(user); db.session.commit()
        session.clear(); session["user_id"] = user.id
        flash("Account created. Welcome to AnswerRank!", "success")
        return redirect(url_for("dashboard"))
    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        user = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash("Incorrect email or password.", "danger")
            return render_template("login.html")
        session.clear(); session["user_id"] = user.id
        return redirect(url_for("dashboard"))
    return render_template("login.html")

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("index"))

@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    if user.role == "teacher":
        classes = Classroom.query.filter_by(teacher_id=user.id).order_by(Classroom.created_at.desc()).all()
        return render_template("teacher_dashboard.html", classes=classes)
    memberships = Membership.query.filter_by(student_id=user.id).all()
    classrooms = [m.classroom for m in memberships]
    submissions = {s.assignment_id:s for s in Submission.query.filter_by(student_id=user.id).all()}
    return render_template("student_dashboard.html", classrooms=classrooms, submissions=submissions)

@app.route("/class/create", methods=["POST"])
@role_required("teacher")
def create_class():
    name = request.form.get("name","").strip()
    subject = request.form.get("subject","").strip() or "General"
    if not name:
        flash("Class name is required.", "danger")
        return redirect(url_for("dashboard"))
    alphabet = string.ascii_uppercase + string.digits
    code = "".join(secrets.choice(alphabet) for _ in range(6))
    while Classroom.query.filter_by(join_code=code).first():
        code = "".join(secrets.choice(alphabet) for _ in range(6))
    db.session.add(Classroom(name=name, subject=subject, join_code=code, teacher_id=current_user().id))
    db.session.commit()
    flash(f"Class created. Share join code {code} with your students.", "success")
    return redirect(url_for("dashboard"))

@app.route("/class/join", methods=["POST"])
@role_required("student")
def join_class():
    code = request.form.get("join_code","").strip().upper()
    classroom = Classroom.query.filter_by(join_code=code).first()
    if not classroom:
        flash("That class code was not found. Check with your teacher.", "danger")
    elif membership_for(classroom.id, current_user().id):
        flash("You have already joined this class.", "info")
    else:
        db.session.add(Membership(classroom_id=classroom.id, student_id=current_user().id))
        db.session.commit()
        flash(f"You joined {classroom.name}.", "success")
    return redirect(url_for("dashboard"))

@app.route("/class/<int:class_id>")
@login_required
def view_class(class_id):
    classroom = db.session.get(Classroom, class_id) or abort(404)
    user = current_user()
    if user.role == "teacher":
        teacher_owns_classroom(classroom)
        students = [m.student for m in classroom.memberships]
        return render_template("teacher_class.html", classroom=classroom, students=students)
    if not membership_for(classroom.id, user.id): abort(403)
    assignments = Assignment.query.filter_by(classroom_id=classroom.id).order_by(Assignment.created_at.desc()).all()
    submissions = {s.assignment_id:s for s in Submission.query.filter_by(student_id=user.id).all()}
    return render_template("student_class.html", classroom=classroom, assignments=assignments, submissions=submissions)

@app.route("/class/<int:class_id>/assignment/create", methods=["POST"])
@role_required("teacher")
def create_assignment(class_id):
    classroom = db.session.get(Classroom, class_id) or abort(404)
    teacher_owns_classroom(classroom)
    title = request.form.get("title","").strip()
    question = request.form.get("question","").strip()
    reference = request.form.get("reference_answer","").strip()
    rubric = request.form.get("rubric_text","").strip()
    due_date = request.form.get("due_date","").strip()
    if not title or not question or not reference:
        flash("Title, question and reference answer are required.", "danger")
        return redirect(url_for("view_class", class_id=class_id))
    db.session.add(Assignment(classroom_id=classroom.id,title=title,question=question,reference_answer=reference,rubric_text=rubric,due_date=due_date))
    db.session.commit()
    flash("Assignment published.", "success")
    return redirect(url_for("view_class", class_id=class_id))

@app.route("/assignment/<int:assignment_id>", methods=["GET","POST"])
@role_required("student")
def submit_assignment(assignment_id):
    assignment = db.session.get(Assignment, assignment_id) or abort(404)
    if not membership_for(assignment.classroom_id, current_user().id): abort(403)
    submission = Submission.query.filter_by(assignment_id=assignment.id, student_id=current_user().id).first()
    if request.method == "POST":
        answer = request.form.get("answer","").strip()
        if len(answer) < 5:
            flash("Please write a fuller answer before submitting.", "danger")
            return render_template("submit.html", assignment=assignment, submission=submission)
        sim, rub, clarity, total, feedback = auto_grade(assignment, answer)
        if submission:
            submission.answer=answer; submission.similarity=sim; submission.rubric_score=rub; submission.clarity=clarity; submission.total_score=total; submission.feedback=feedback; submission.graded=False; submission.teacher_feedback=""
        else:
            submission=Submission(assignment_id=assignment.id,student_id=current_user().id,answer=answer,similarity=sim,rubric_score=rub,clarity=clarity,total_score=total,feedback=feedback)
            db.session.add(submission)
        db.session.commit()
        flash("Answer submitted. The automatic score is provisional until your teacher reviews it.", "success")
        return redirect(url_for("submit_assignment", assignment_id=assignment.id))
    return render_template("submit.html", assignment=assignment, submission=submission)

@app.route("/class/<int:class_id>/submissions")
@role_required("teacher")
def class_submissions(class_id):
    classroom = db.session.get(Classroom, class_id) or abort(404)
    teacher_owns_classroom(classroom)
    assignments = Assignment.query.filter_by(classroom_id=classroom.id).all()
    selected_id = request.args.get("assignment", type=int)
    selected = next((a for a in assignments if a.id == selected_id), assignments[0] if assignments else None)
    submissions = Submission.query.filter_by(assignment_id=selected.id).order_by(Submission.total_score.desc()).all() if selected else []
    return render_template("submissions.html", classroom=classroom, assignments=assignments, selected=selected, submissions=submissions)

@app.route("/submission/<int:submission_id>/grade", methods=["POST"])
@role_required("teacher")
def grade_submission(submission_id):
    submission = db.session.get(Submission, submission_id) or abort(404)
    teacher_owns_classroom(submission.assignment.classroom)
    try:
        score = float(request.form.get("score",""))
    except ValueError:
        flash("Score must be a number between 0 and 100.", "danger")
        return redirect(url_for("class_submissions", class_id=submission.assignment.classroom_id, assignment=submission.assignment_id))
    if not 0 <= score <= 100:
        flash("Score must be between 0 and 100.", "danger")
        return redirect(url_for("class_submissions", class_id=submission.assignment.classroom_id, assignment=submission.assignment_id))
    submission.total_score = round(score, 1)
    submission.teacher_feedback = request.form.get("teacher_feedback","").strip()[:3000]
    submission.graded = True
    db.session.commit()
    flash("Teacher grade saved and visible to the student.", "success")
    return redirect(url_for("class_submissions", class_id=submission.assignment.classroom_id, assignment=submission.assignment_id))

@app.route("/class/<int:class_id>/delete", methods=["POST"])
@role_required("teacher")
def delete_class(class_id):
    classroom = db.session.get(Classroom, class_id) or abort(404)
    teacher_owns_classroom(classroom)
    db.session.delete(classroom); db.session.commit()
    flash("Class and its assignments were deleted.", "success")
    return redirect(url_for("dashboard"))

@app.errorhandler(403)
def forbidden(_e):
    return render_template("error.html", code=403, message="You don't have permission to access this page."), 403

@app.errorhandler(404)
def not_found(_e):
    return render_template("error.html", code=404, message="The page or record could not be found."), 404

@app.get("/health")
def health():
    return {"status":"ok", "service":"AnswerRank Classroom"}

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT","5000")), debug=os.environ.get("FLASK_DEBUG")=="1")
