import os
import logging
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# ---------- 日志 ----------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24).hex())

# ---------- 持久化 ----------
basedir = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(basedir, 'instance')
UPLOAD_DIR = os.path.join(basedir, 'static', 'uploads')
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ----- 数据库 (PostgreSQL / SQLite) -----
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL:
    # Zeabur 等云平台使用 PostgreSQL
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
else:
    # 本地开发使用 SQLite
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(DATA_DIR, 'works.db')

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB

# ----- S3 对象存储配置 -----
S3_ENABLED = bool(os.environ.get('S3_BUCKET'))
if S3_ENABLED:
    import boto3
    s3_client = boto3.client(
        's3',
        endpoint_url=os.environ.get('S3_ENDPOINT'),
        region_name=os.environ.get('S3_REGION', 'auto'),
        aws_access_key_id=os.environ.get('S3_ACCESS_KEY'),
        aws_secret_access_key=os.environ.get('S3_SECRET_KEY'),
    )
    S3_BUCKET = os.environ['S3_BUCKET']
    S3_PUBLIC_URL = os.environ.get('S3_PUBLIC_URL', '').rstrip('/')
    logger.info(f"S3 存储已启用，桶: {S3_BUCKET}")
else:
    s3_client = None
    S3_BUCKET = ''
    S3_PUBLIC_URL = ''
    logger.info("使用本地文件存储")

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'mp4', 'mov', 'avi', 'webm'}

db = SQLAlchemy(app)

# ---------- 数据库模型 ----------
class Work(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    author = db.Column(db.String(80), nullable=False, default='匿名')
    category = db.Column(db.String(50), default='')
    description = db.Column(db.Text, default='')
    content = db.Column(db.Text, default='')
    image = db.Column(db.String(300), default='')
    file = db.Column(db.String(300), default='')
    is_video = db.Column(db.Boolean, default=False)
    votes = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default='pending')
    is_recommended = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    is_super = db.Column(db.Boolean, default=False)

class Reviewer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    has_voted = db.Column(db.Boolean, default=False)

class Setting(db.Model):
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.Text, default='')

# ---------- 文件存储抽象层 ----------

def _save_file_local(filename, file_obj):
    """保存文件到本地"""
    dest = os.path.join(UPLOAD_DIR, filename)
    file_obj.save(dest)
    logger.info(f"文件已保存到本地: {filename}")

def _delete_file_local(filename):
    """删除本地文件"""
    try:
        os.remove(os.path.join(UPLOAD_DIR, filename))
    except Exception:
        pass

def _save_file_s3(filename, file_obj):
    """上传文件到 S3"""
    file_obj.seek(0)
    s3_client.upload_fileobj(file_obj, S3_BUCKET, 'uploads/' + filename,
                             ExtraArgs={'ACL': 'public-read'})
    logger.info(f"文件已上传到 S3: {filename}")

def _delete_file_s3(filename):
    """从 S3 删除文件"""
    try:
        s3_client.delete_object(Bucket=S3_BUCKET, Key='uploads/' + filename)
    except Exception:
        pass

def save_upload(filename, file_obj):
    """保存上传的文件（自动选择 S3 或本地）"""
    filename = secure_filename(filename)
    if S3_ENABLED:
        _save_file_s3(filename, file_obj)
    else:
        _save_file_local(filename, file_obj)
    return filename

def delete_upload(filename):
    """删除上传的文件"""
    if not filename:
        return
    if S3_ENABLED:
        _delete_file_s3(filename)
    else:
        _delete_file_local(filename)

def get_file_url(filename):
    """获取文件的公开访问 URL"""
    if not filename:
        return ''
    if S3_ENABLED:
        return f"{S3_PUBLIC_URL}/uploads/{filename}"
    return url_for('static', filename='uploads/' + filename)

# Jinja2 模板上下文：所有模板都可以使用 file_url() 函数
@app.context_processor
def inject_file_url():
    return dict(file_url=get_file_url)

def make_upload_filename(original_name, used_names=None):
    """生成唯一的文件名（处理重名冲突）"""
    name = secure_filename(original_name)
    if used_names is None:
        used_names = set()
    base, ext = os.path.splitext(name)
    counter = 1
    while name in used_names or _file_exists(name):
        name = f"{base}_{counter}{ext}"
        counter += 1
    return name

def _file_exists(filename):
    """检查文件是否已存在（S3 或本地）"""
    if S3_ENABLED:
        try:
            s3_client.head_object(Bucket=S3_BUCKET, Key='uploads/' + filename)
            return True
        except Exception:
            return False
    else:
        return os.path.exists(os.path.join(UPLOAD_DIR, filename))

# ---------- 通用函数 ----------
def get_setting(key):
    s = Setting.query.filter_by(key=key).first()
    return s.value if s else ''

def set_setting(key, val):
    s = Setting.query.filter_by(key=key).first()
    if s:
        s.value = val
    else:
        db.session.add(Setting(key=key, value=val))
    db.session.commit()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def is_video_file(filename):
    ext = filename.rsplit('.', 1)[1].lower()
    return ext in {'mp4', 'mov', 'avi', 'webm'}

# ---------- 初始化 ----------
def init_db():
    db.create_all()
    if not Admin.query.filter_by(username='admin').first():
        db.session.add(Admin(username='admin', password=generate_password_hash('admin123'), is_super=True))
    if not Reviewer.query.filter_by(username='reviewer1').first():
        db.session.add(Reviewer(username='reviewer1', password=generate_password_hash('review123')))
    if not db.session.get(Setting, 'review_open'):
        set_setting('review_open', 'true')
    if not db.session.get(Setting, 'results_published'):
        set_setting('results_published', 'false')
    if not db.session.get(Setting, 'declaration_content'):
        set_setting('declaration_content', '<div class="statement-container"><p>本活动……（原声明内容保持不变）</p></div>')
    if not db.session.get(Setting, 'recruit_content'):
        set_setting('recruit_content', '<div class="recruit-container"><h3>关于组建……（原内容保持不变）</h3>……</div>')
    db.session.commit()
    logger.info("数据库初始化完成")

def self_check():
    logger.info("开始自检...")
    if not os.path.exists(DATA_DIR):
        logger.error(f"数据目录 {DATA_DIR} 不存在")
    else:
        logger.info(f"数据目录 {DATA_DIR} 存在")
    if not os.path.exists(UPLOAD_DIR):
        logger.error(f"上传目录 {UPLOAD_DIR} 不存在")
    else:
        logger.info(f"上传目录 {UPLOAD_DIR} 存在")
    try:
        db.create_all()
        logger.info("数据库表已就绪")
    except Exception as e:
        logger.error(f"数据库表创建失败: {e}")
    required_fields = ['title', 'author', 'category', 'is_video']
    for field in required_fields:
        if not hasattr(Work, field):
            logger.error(f"Work 模型缺少字段 {field}")
    logger.info("自检完成")

# ---------- 路由 ----------
@app.route('/')
def index():
    works = Work.query.order_by(Work.created_at.desc()).all()
    works_count = len(works)
    total_votes = sum(w.votes for w in works)
    results_published_str = get_setting('results_published')
    results_published_bool = (results_published_str == 'true')
    if results_published_bool:
        image_categories = ['摄影', '海报', '绘画', '手工制品']
        text_categories = ['征文', '诗歌']
        sections = []
        for cat in image_categories:
            cat_works = Work.query.filter_by(category=cat, status='approved').order_by(Work.votes.desc()).limit(8).all()
            if cat_works:
                sections.append({'title': cat, 'works': cat_works, 'type': 'image'})
        text_works = Work.query.filter(Work.category.in_(text_categories), Work.status == 'approved').order_by(Work.votes.desc()).limit(6).all()
        if text_works:
            sections.append({'title': '文字作品', 'works': text_works, 'type': 'text'})
        return render_template('index.html', sections=sections, results_published=True,
                               works_count=works_count, total_votes=total_votes)
    else:
        return render_template('index.html', works=works, results_published=False,
                               works_count=works_count, total_votes=total_votes)

@app.route('/all_works')
def all_works():
    works = Work.query.filter_by(status='approved').order_by(Work.created_at.desc()).all()
    return render_template('all_works.html', works=works)

@app.route('/work/<int:work_id>')
def work_detail(work_id):
    work = db.session.get(Work, work_id)
    if not work:
        return jsonify({'error': 'not found'}), 404
    return jsonify({
        'id': work.id,
        'title': work.title,
        'author': work.author,
        'category': work.category,
        'description': work.description,
        'content': work.content,
        'image': get_file_url(work.image),
        'file': get_file_url(work.file),
        'image_name': work.image,
        'file_name': work.file,
        'is_video': work.is_video,
        'votes': work.votes
    })

@app.route('/reviewer_login', methods=['GET', 'POST'])
def reviewer_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        reviewer = Reviewer.query.filter_by(username=username).first()
        if reviewer and check_password_hash(reviewer.password, password):
            session['reviewer_id'] = reviewer.id
            session['reviewer_username'] = reviewer.username
            return redirect(url_for('review'))
        flash('用户名或密码错误', 'error')
    return render_template('reviewer_login.html')

@app.route('/review', methods=['GET', 'POST'])
def review():
    if 'reviewer_id' not in session:
        return redirect(url_for('reviewer_login'))
    reviewer = db.session.get(Reviewer, session['reviewer_id'])
    if not reviewer:
        session.pop('reviewer_id', None)
        return redirect(url_for('reviewer_login'))
    if reviewer.has_voted:
        flash('您已经投过票了', 'warning')
        return redirect(url_for('index'))
    if get_setting('review_open') != 'true':
        flash('评审通道已关闭', 'warning')
        return redirect(url_for('index'))
    if request.method == 'POST':
        selected_ids = request.form.getlist('works')
        for wid in selected_ids:
            work = db.session.get(Work, int(wid))
            if work:
                work.votes += 1
        reviewer.has_voted = True
        db.session.commit()
        flash('评审提交成功', 'success')
        return redirect(url_for('index'))
    works = Work.query.all()
    return render_template('review.html', works=works)

@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        admin = Admin.query.filter_by(username=username).first()
        if admin and check_password_hash(admin.password, password):
            session['admin_id'] = admin.id
            session['admin_username'] = admin.username
            session['is_super'] = admin.is_super
            flash('登录成功', 'success')
            return redirect(url_for('admin'))
        flash('用户名或密码错误', 'error')
    return render_template('admin_login.html')

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if 'admin_id' not in session:
        return redirect(url_for('admin_login'))
    if request.method == 'POST':
        # 批量上传
        if 'batch_upload' in request.form:
            titles = request.form.getlist('title[]')
            authors = request.form.getlist('author[]')
            categories = request.form.getlist('category[]')
            descriptions = request.form.getlist('description[]')
            contents = request.form.getlist('content[]')
            image_files = request.files.getlist('image[]')
            file_files = request.files.getlist('file[]')
            used_names = set()
            success_count = 0
            for i in range(len(titles)):
                title = titles[i].strip()
                if not title:
                    continue
                author = authors[i].strip() if i < len(authors) else '匿名'
                category = categories[i].strip() if i < len(categories) else ''
                description = descriptions[i].strip() if i < len(descriptions) else ''
                content = contents[i].strip() if i < len(contents) else ''
                img_name = ''
                is_vid = False
                if i < len(image_files) and image_files[i] and image_files[i].filename:
                    f = image_files[i]
                    if allowed_file(f.filename):
                        img_name = make_upload_filename(f.filename, used_names)
                        used_names.add(img_name)
                        save_upload(img_name, f)
                        is_vid = is_video_file(img_name)
                file_name = ''
                if i < len(file_files) and file_files[i] and file_files[i].filename:
                    f = file_files[i]
                    file_name = make_upload_filename(f.filename, used_names)
                    used_names.add(file_name)
                    save_upload(file_name, f)
                work = Work(title=title, author=author, category=category,
                            description=description, content=content,
                            image=img_name, file=file_name, is_video=is_vid,
                            status='pending')
                db.session.add(work)
                success_count += 1
            db.session.commit()
            flash(f'批量上传成功，共 {success_count} 个作品', 'success')
            return redirect(url_for('admin'))

        # 单作品上传
        elif 'upload' in request.form:
            title = request.form['title']
            author = request.form['author']
            category = request.form['category']
            description = request.form.get('description', '')
            content = request.form.get('content', '')
            image_file = request.files.get('image')
            file_file = request.files.get('file')
            image_name = ''
            file_name = ''
            if image_file and image_file.filename and allowed_file(image_file.filename):
                image_name = save_upload(image_file.filename, image_file)
            if file_file and file_file.filename:
                file_name = save_upload(file_file.filename, file_file)
            is_vid = is_video_file(image_name) if image_name else False
            work = Work(title=title, author=author, category=category,
                        description=description, content=content,
                        image=image_name, file=file_name, is_video=is_vid,
                        status='pending')
            db.session.add(work)
            db.session.commit()
            flash('作品上传成功', 'success')

        elif 'delete_work' in request.form:
            work_id = int(request.form['delete_work'])
            work = db.session.get(Work, work_id)
            if work:
                delete_upload(work.image)
                delete_upload(work.file)
                db.session.delete(work)
                db.session.commit()
                flash('作品已删除', 'success')

        elif session.get('is_super'):
            if 'toggle_review' in request.form:
                current = get_setting('review_open')
                set_setting('review_open', 'false' if current == 'true' else 'true')
                flash('评审通道状态已切换', 'success')
            elif 'publish_results' in request.form:
                current = get_setting('results_published')
                set_setting('results_published', 'true' if current != 'true' else 'false')
                flash('结果发布状态已切换', 'success')
            elif 'reset_votes' in request.form:
                Work.query.update({Work.votes: 0})
                Reviewer.query.update({Reviewer.has_voted: False})
                db.session.commit()
                flash('投票数据已重置', 'success')
            elif 'add_reviewer' in request.form:
                username = request.form['username']
                password = request.form['password']
                if not Reviewer.query.filter_by(username=username).first():
                    db.session.add(Reviewer(username=username, password=generate_password_hash(password)))
                    db.session.commit()
                    flash('评审员已添加', 'success')
                else:
                    flash('用户名已存在', 'error')
            elif 'edit_declaration' in request.form:
                set_setting('declaration_content', request.form['declaration_content'])
                flash('声明内容已更新', 'success')
            elif 'edit_recruit' in request.form:
                set_setting('recruit_content', request.form['recruit_content'])
                flash('宣讲团内容已更新', 'success')
            elif 'add_admin' in request.form:
                username = request.form['new_admin_username']
                password = request.form['new_admin_password']
                if not Admin.query.filter_by(username=username).first():
                    db.session.add(Admin(username=username, password=generate_password_hash(password)))
                    db.session.commit()
                    flash('管理员已添加', 'success')
                else:
                    flash('用户名已存在', 'error')
        else:
            flash('无权限执行该操作', 'error')
        return redirect(url_for('admin'))

    works = Work.query.order_by(Work.created_at.desc()).all()
    reviewers = Reviewer.query.all()
    decl_setting = Setting.query.filter_by(key='declaration_content').first()
    rec_setting = Setting.query.filter_by(key='recruit_content').first()
    review_open = get_setting('review_open')
    results_published = get_setting('results_published')
    return render_template('admin.html',
                           works=works, reviewers=reviewers,
                           declaration=decl_setting, recruit_content=rec_setting,
                           review_open=review_open, results_published=results_published)

@app.route('/admin/approve/<int:work_id>', methods=['POST'])
def approve_work(work_id):
    if not session.get('is_super'):
        flash('无权限执行该操作', 'error')
        return redirect(url_for('admin'))
    work = db.session.get(Work, work_id)
    if work:
        work.status = 'approved'
        db.session.commit()
    return redirect(url_for('admin'))

@app.route('/admin/reject/<int:work_id>', methods=['POST'])
def reject_work(work_id):
    if not session.get('is_super'):
        flash('无权限执行该操作', 'error')
        return redirect(url_for('admin'))
    work = db.session.get(Work, work_id)
    if work:
        work.status = 'rejected'
        db.session.commit()
    return redirect(url_for('admin'))

@app.route('/admin/recommend/<int:work_id>', methods=['POST'])
def recommend_work(work_id):
    if not session.get('is_super'):
        flash('无权限执行该操作', 'error')
        return redirect(url_for('admin'))
    work = db.session.get(Work, work_id)
    if work:
        work.is_recommended = True
        db.session.commit()
    return redirect(url_for('admin'))

@app.route('/admin/unrecommend/<int:work_id>', methods=['POST'])
def unrecommend_work(work_id):
    if not session.get('is_super'):
        flash('无权限执行该操作', 'error')
        return redirect(url_for('admin'))
    work = db.session.get(Work, work_id)
    if work:
        work.is_recommended = False
        db.session.commit()
    return redirect(url_for('admin'))

@app.route('/recruit')
def recruit():
    content = get_setting('recruit_content') or '暂无内容'
    return render_template('recruit.html', content=content)

@app.route('/statement')
def statement():
    content = get_setting('declaration_content') or '暂无声明内容'
    return render_template('statement.html', content=content)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    with app.app_context():
        init_db()
        self_check()
    port = int(os.environ.get('PORT', 8080))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(debug=debug, host='0.0.0.0', port=port)
