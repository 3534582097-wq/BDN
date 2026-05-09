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

# ---- 数据持久化（Zeabur 持久卷挂载点） ----
basedir = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(basedir, 'data')                 # 挂载 /app/data
UPLOAD_DIR = os.path.join(basedir, 'static', 'uploads')  # 挂载 /app/static/uploads
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///' + os.path.join(DATA_DIR, 'works.db'))
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = UPLOAD_DIR
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB

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

# ---------- 通用函数 ----------
def get_setting(key):
    s = db.session.get(Setting, key)
    return s.value if s else ''

def set_setting(key, val):
    s = db.session.get(Setting, key)
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

def make_upload_filename(original_name, used_names=None):
    """生成唯一的文件名（处理重名冲突）"""
    name = secure_filename(original_name)
    if used_names is None:
        used_names = set()
    base, ext = os.path.splitext(name)
    counter = 1
    while name in used_names or os.path.exists(os.path.join(UPLOAD_DIR, name)):
        name = f"{base}_{counter}{ext}"
        counter += 1
    return name

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
        set_setting('declaration_content',
            '<style> .statement-container { max-width: 800px; margin: 40px auto; padding: 30px 35px; font-family: \'Segoe UI\', \'Microsoft YaHei\', sans-serif; line-height: 1.8; color: #2c3e50; background: #ffffff; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); border: 1px solid #e8ecef; } .statement-container p { margin: 16px 0; text-indent: 2em; position: relative; padding-left: 0; } .statement-container p::before { content: "●"; color: #3ba86c; font-size: 10px; position: absolute; left: 1.2em; top: 0.4em; } .statement-container p:first-child { margin-top: 0; } .statement-container p:last-child { margin-bottom: 0; } .statement-container strong { color: #1e4a3b; } @media (max-width: 600px) { .statement-container { padding: 20px 16px; margin: 20px 10px; } } </style> <div class="statement-container"> <p>本活动"微光成炬，羽映万生"生态公益宣讲团及作品征集由林学院主办，旨在提高师生对鸟类保护及生态环境的关注。所有投稿作品须为原创，不得侵犯他人著作权。主办方有权在非商业用途下对作品进行展示、宣传，使用时将注明作者。</p> <p>本次活动公告、宣发内容解释权由林学院综合素质考评中心持有。</p> <p>本活动优秀作品评选结果解释权由林学院综合素质考评中心所有。</p> </div>')
    if not db.session.get(Setting, 'recruit_content'):
        set_setting('recruit_content',
            '<style> .recruit-container { max-width: 900px; margin: 0 auto; padding: 30px 20px; font-family: \'Segoe UI\', \'Microsoft YaHei\', sans-serif; line-height: 1.8; color: #333; background: #fafbfc; border-radius: 16px; box-shadow: 0 4px 20px rgba(0,60,30,0.08); } .recruit-container h3 { font-size: 22px; color: #1e4a3b; border-left: 5px solid #3ba86c; padding-left: 16px; margin-top: 0; margin-bottom: 20px; } .recruit-container h4 { font-size: 18px; color: #1e4a3b; margin-top: 28px; margin-bottom: 10px; border-bottom: 2px solid #d4e8d8; padding-bottom: 6px; } .recruit-container p { margin: 10px 0; } .recruit-container ol { padding-left: 24px; margin: 12px 0; } .recruit-container ol li { margin-bottom: 10px; padding-left: 8px; } .recruit-container strong { color: #c0392b; font-weight: 600; } .recruit-container a { color: #2a7a4b; text-decoration: none; font-weight: 600; } .recruit-container a:hover { text-decoration: underline; } .recruit-container .note { color: #7f8c8d; font-size: 0.9em; border-top: 1px dashed #ccc; padding-top: 14px; margin-top: 24px; } @media (max-width: 600px) { .recruit-container { padding: 16px; } .recruit-container h3 { font-size: 18px; } } </style> <div class="recruit-container"> <h3>关于组建林学院"微光成炬，羽映万生"世界候鸟日与世界生物多样性日活动生态公益宣讲团的通知</h3> <p>为切实提升"微光成炬，羽映万生"世界候鸟日与世界生物多样性日主题作品征集宣传活动的公益属性与时代价值，担负起生态文明教育与人才培养方面的独特使命，更好地以青春创意响应全球生态保护号召，特组建本批生态公益宣讲团。</p> <h4>一、招募人数</h4> <p>6‑7 人</p> <h4>二、面向对象</h4> <p>林学院 24、25 级本科生</p> <h4>三、招募要求</h4> <ol> <li>热爱生态文明事业，对自然教育、生态保护教育有浓厚兴趣。</li> <li>政治立场坚定，拥护且自觉践行习近平生态文明思想，无不良行为与失信记录。</li> <li>具备良好的语言表达能力和感染力，善于用青少年喜闻乐见的方式传授候鸟保护与生物多样性知识。</li> <li>对候鸟保护与世界多样性保护有一定的知识基础，并且乐于在备课实践中持续学习，将知识融会贯通。</li> <li>对宣讲任务有责任心，有时间精力参与备课与授课全过程，坚持一始而终，对青少年有善心、有耐心、有爱心，对自己能完成授课有信心。</li> <li>有主持、演讲、志愿服务和相关实践经历者优先。</li> </ol> <h4>四、报名方式</h4> <p><strong>附件："微光成炬，羽映万生"生态公益宣讲团报名表</strong></p> <p>报名者需要如实填写报名表，文件命名为"姓名+宣讲团报名"，于 <strong>5 月 11 日 18:00 前</strong> 发送至指定邮箱 <a href="mailto:3534582097@qq.com">3534582097@qq.com</a>。若由于超时、联系信息填写错误、邮件地址填写错误等原因导致的报名失败，我方将不接受申诉，附件及详细联系方式查见通知群。</p> <p class="note">本活动最终解释权归林学院综合素质考评中心所有。</p> </div>')
    db.session.commit()
    logger.info("数据库初始化完成")

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
        'image': work.image,
        'file': work.file,
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
        flash('您已经投过票了，每个评审员仅能投票一次', 'warning')
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
        flash('评审提交成功，感谢您的参与！', 'success')
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
                        f.save(os.path.join(UPLOAD_DIR, img_name))
                        is_vid = is_video_file(img_name)
                file_name = ''
                if i < len(file_files) and file_files[i] and file_files[i].filename:
                    f = file_files[i]
                    file_name = make_upload_filename(f.filename, used_names)
                    used_names.add(file_name)
                    f.save(os.path.join(UPLOAD_DIR, file_name))
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
                image_name = secure_filename(image_file.filename)
                image_file.save(os.path.join(UPLOAD_DIR, image_name))
            if file_file and file_file.filename:
                file_name = secure_filename(file_file.filename)
                file_file.save(os.path.join(UPLOAD_DIR, file_name))
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
                for fname in (work.image, work.file):
                    if fname:
                        try:
                            os.remove(os.path.join(UPLOAD_DIR, fname))
                        except:
                            pass
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
    admins = Admin.query.all()
    decl_setting = db.session.get(Setting, 'declaration_content')
    rec_setting = db.session.get(Setting, 'recruit_content')
    review_open = get_setting('review_open')
    results_published = get_setting('results_published')
    return render_template('admin.html',
                           works=works, reviewers=reviewers, admins=admins,
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
    port = int(os.environ.get('PORT', 8080))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(debug=debug, host='0.0.0.0', port=port)
