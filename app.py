import os

from flask import Flask, render_template, g, session, request, jsonify, flash, redirect

from flask_debugtoolbar import DebugToolbarExtension
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
import requests

from forms import UserAddForm, LoginForm, CourseAddForm, CourseSearchForm

from models import db, connect_db, User, Course, Video, VideoCourse

# comment this line out when deploying to Heroku
# from secrets import API_SECRET_KEY

# comment this line out when working with local app
API_SECRET_KEY = os.environ.get('API_SECRET_KEY')

CURR_USER_KEY = "curr_user"
API_BASE_URL = "https://www.googleapis.com/youtube/v3"

app = Flask(__name__)

# Get DB_URI from environ variable (useful for production/testing) or, if not set there, use development local db.

app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'postgres:///success_world')

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ECHO'] = False
# redirects must be intercepted for some tests to pass
app.config['DEBUG_TB_INTERCEPT_REDIRECTS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', "another543256432secret")

toolbar = DebugToolbarExtension(app)

connect_db(app)


@app.before_request
def add_user_to_g():
    """If we're logged in, add curr user to Flask global."""
    if CURR_USER_KEY in session:
        g.user = User.query.get(session[CURR_USER_KEY])
    else:
        g.user = None


# *******************************
# API ENDPOINT ROUTE
# *******************************

@app.route("/api/get-videos", methods=["GET", "POST"])
def search_videos():
    """API endpoint.
    This route has no view.
    Get videos from YouTube based on topic entered in search field."""

    if not g.user:
        flash("Success unauthorized.", "danger")
        return redirect("/")

    # get search form data
    data = get_form_data()
    keyword = data["keyword"]

    # validate the form data
    errors = validate_data(data)

    # if errors, return them
    if errors['errors']:
        return errors

    # no errors in data; get videos for the keyword searched
    res = get_yt_videos(keyword)

    return res


# *******************************
# USER ROUTES
# *******************************

@app.route('/users/demo')
def make_demo_acct():
    """This route has no view.
    Create a demo account for an anonymous user."""

    demo_old = User.query.filter_by(username="Demo", email="demo@demo.com").delete()
    user = signup_demo_user()

    do_login(user)
    g.user = user

    populate_demo_data()

    return redirect("/")

@app.route('/users/new', methods=["GET", "POST"])
def signup():
    """Handle user signup.

    Create new user and add to db.
    Log the user in and redirect to home page.

    If form not valid, re-present form.

    If there already is a user with that username or email: flash message and re-present form.
    """

    do_logout()
    form = UserAddForm()

    if form.validate_on_submit():
        try:
            user = User.signup(
                username=form.username.data,
                password=form.password.data,
                first_name=form.first_name.data,
                last_name=form.last_name.data,
                image_url=form.image_url.data or User.image_url.default.arg,
                email=form.email.data,
            )
            db.session.commit()

        except IntegrityError as e:
            flash("Username or email already taken", 'danger')
            return render_template('users/signup.html', form=form)

        do_login(user)
        flash(f"Welcome {user.username}!")
        return redirect("/")

    else:
        return render_template('users/signup.html', form=form)


@app.route('/users/login', methods=["GET", "POST"])
def login():
    """Handle user login."""

    form = LoginForm()

    if form.validate_on_submit():
        user = User.authenticate(username=form.username.data,
                                 password=form.password.data)

        if user:
            do_login(user)
            flash(f"Hello, {user.username}!", "success")
            return redirect("/")

        flash("Invalid credentials.", 'danger')

    return render_template('users/login.html', form=form)


@app.route('/logout')
def logout():
    """Handle logout of user."""

    do_logout()

    flash("You have successfully logged out.", 'success')
    return redirect("/users/login")


@app.route('/users/<int:user_id>/courses')
def list_user_courses(user_id):
    """Show a list of courses created by the logged in user."""

    if not g.user:
        flash("Success unauthorized.", "danger")
        return redirect("/")

    if user_id != g.user.id:
        flash("Success unauthorized.", "danger")
        return redirect("/")

    courses = Course.query.filter(
        Course.creator_id == user_id).all()

    return render_template(f"users/courses.html", courses=courses)


# *******************************
# VIDEO ROUTES
# *******************************

@app.route("/courses/<int:course_id>/videos/search", methods=["GET"])
def search_videos_form(course_id):
    """Display keyword search form and search results."""

    # JavaScript is handling the form submission from this page.
    # Flask API is handling the calls to YouTube Data API.

    if not g.user:
        flash("Success unauthorized.", "danger")
        return redirect("/")

    course = Course.query.get_or_404(course_id)

    if course.creator_id != g.user.id:
        flash("You must be the course creator to view this page.", "danger")
        return redirect("/")

    return render_template('/videos/search.html', course=course)


@app.route("/courses/<int:course_id>/videos/<yt_video_id>/add", methods=["POST"])
def add_video_to_course(course_id, yt_video_id):
    """This route does not have a view.
    Check to see if the video is in the database already.
    If not, add the video to database.
    Check to see if the video is part of the course already.
    If not, add the video to the course.
    Add video sequence number (within the course) to the database.
    Redirect back to video search page."""

    course = Course.query.get_or_404(course_id)

    if not g.user:
        flash("Success unauthorized.", "danger")
        return redirect("/")

    if course.creator_id != g.user.id:
        flash("Success unauthorized", "danger")
        return redirect("/")

    # create video & add to db if not already there
    form_data = request.form
    video = add_video_to_db(form_data, yt_video_id)

    # Query the db for this course
    course = Course.query.get_or_404(course_id)

    # is the video already part of the course?
    if video in course.videos:
        flash("This video has already been added to the course.", "warning")
        return redirect(f'../../../../courses/{course_id}/videos/search')

    video_seq = len(course.videos) + 1

    # CHANGE: QUESTION: is this the best way to add a record to a join table???
    video_course = VideoCourse(course_id=course_id,
                               video_id=video.id,
                               video_seq=video_seq)

    db.session.add(video_course)
    db.session.commit()

    flash("Good news! The video was successfully added to the course.", "success")

    return redirect(f'../../../../courses/{course_id}/videos/search')


# *******************************
# COURSE ROUTES
# *******************************

@app.route("/courses/new", methods=["GET", "POST"])
def courses_add():
    """Create a new course:

    If GET: Show the course add form.
    If POST and form validates:
        * if course title does not exist yet for this creator: add course and redirect to videos search page
        * if course does exist already for this creator:
        flash a message notifying the user of this
    If POST and form does not validate, re-present form."""

    if not g.user:
        flash("Success unauthorized.", "danger")
        return redirect("/")

    form = CourseAddForm()

    # form validation
    if form.validate_on_submit():
        # check to see if course already exists for this creator
        course = Course.query.filter(
            Course.title == form.title.data, Course.creator_id == g.user.id).first()

        # if course already exists
        if course:
            flash("You have already created a course with this name. Please choose a new name.", "warning")        
        # if course does not yet exist, create it & save to db
        else:
            course = Course(title=form.title.data,
                            description=form.description.data,
                            creator_id=g.user.id)
            db.session.add(course)
            db.session.commit()
            flash(
                f'Your course "{course.title}" was created successfully.', 'success')

            return redirect(f'/courses/{course.id}/videos/search')

    return render_template("courses/new.html", form=form)


@app.route("/courses/search", methods=["GET", "POST"])
def courses_search():
    """Show course search form.
    Get the title to search for.
    Return cards for matching course(s)."""

    if not g.user:
        flash("Success unauthorized.", "danger")
        return redirect("/")

    form = CourseSearchForm()
    courses = Course.query.all()

    if form.validate_on_submit():
        phrase = (form.phrase.data)
        phrase_lower = phrase.lower()
        # if no search phrase was provided by user
        if not phrase_lower:
            flash('No search term found; showing all courses', "info")
        # if search phrase was provided by user
        else:
            courses = Course.query.filter(func.lower(Course.title).contains(f"{phrase_lower}")).all()
            # if no courses were returned from the search
            if len(courses) == 0:
                flash(
                    f'There are no courses with titles similar to {phrase}.', "warning")
            # if courses match the search
            else:
                flash(
                    f'Showing courses with titles matching phrases similar to {phrase}', "info")

    return render_template('/courses/search.html', form=form, courses=courses)


@app.route('/courses/<int:course_id>/edit', methods=["GET"])
def courses_edit(course_id):
    """Display the videos in the course.
    Courses may be added, removed, or resequenced.
    Edit an existing course."""

    if not g.user:
        flash("Success unauthorized.", "danger")
        return redirect("/")

    course = Course.query.get_or_404(course_id)

    # restrict Success to the creator of this course
    if course.creator_id != g.user.id:
        flash("You must be the course creator to view this page.", "danger")
        return redirect("/")

    videos_courses_asc = (VideoCourse
                          .query
                          .filter(VideoCourse.course_id == course_id)
                          .order_by(VideoCourse.video_seq)
                          .all())

    return render_template("courses/edit.html", course=course, videos_courses=videos_courses_asc)


@app.route('/courses/<int:course_id>/details', methods=["GET"])
def courses_details(course_id):
    """Display the videos in the course."""

    if not g.user:
        flash("Success unauthorized.", "danger")
        return redirect("/")

    course = Course.query.get_or_404(course_id)
    videos_courses_asc = (VideoCourse
                          .query
                          .filter(VideoCourse.course_id == course_id)
                          .order_by(VideoCourse.video_seq)
                          .all())
    user = g.user
    return render_template("courses/details.html", user=user, course=course, videos_courses=videos_courses_asc)


@app.route('/courses/<int:course_id>/videos/resequence', methods=["POST"])
def courses_resequence(course_id):
    """There is no view for this route.
    Resequence the videos within a course.
    """

    if not g.user:
        flash("Success unauthorized.", "danger")
        return redirect("/")

    # Query to get the course
    course = Course.query.get_or_404(course_id)

    if course.creator_id != g.user.id:
        flash("Success unauthorized", "danger")
        return redirect("/")

    # get the video data from the form
    video_id = request.form.get('video-id')
    vc_id = request.form.get('vc-id')
    video_seq = int(request.form.get('video-seq'))

    arrow = request.form.get('arrow')
    arrow = int(arrow)


    vc = VideoCourse.query.filter(VideoCourse.id == vc_id).all()

    vc_switch = VideoCourse.query.filter(VideoCourse.course_id == course_id,
        VideoCourse.video_seq == (video_seq + arrow)).all()

    if len(vc) == 1 and len(vc_switch) == 1:

        # update with new video_course_video_seq
        temp_seq_1 = -1
        # curr_seq = video_seq
        vc[0].video_seq = temp_seq_1
        db.session.commit()
        vc_switch[0].video_seq = video_seq
        db.session.commit()
        vc[0].video_seq = video_seq + arrow
        db.session.commit()

    # re-render the course edit page

    return redirect(f'../../../courses/{course_id}/edit')


@app.route('/courses/<int:course_id>/videos/remove', methods=["POST"])
def remove_video(course_id):
    """There is no view for this route.
    Remove a video from a course.
    If the video is only part of one course, also remove video from db.
    """

    if not g.user:
        flash("Success unauthorized.", "danger")
        return redirect("/")

    # Query to get the course
    course = Course.query.get_or_404(course_id)

    if course.creator_id != g.user.id:
        flash("Success unauthorized", "danger")
        return redirect("/")

    # get the video_id & video sequence number from the form
    video_id = request.form.get('video-id')
    video_seq = int(request.form.get('video-seq'))

    videos_courses = VideoCourse.query.filter(
        VideoCourse.video_id == video_id).all()

    # if no other courses use this video, remove the video from the db (the delete will cascade to the videos_courses table)
    if len(videos_courses) == 1:
        Video.query.filter(Video.id == video_id).delete()
        db.session.commit()

    # otherwise leave video in the db and remove the corresponding entry from videos_courses table only
    else:
        VideoCourse.query.filter(
            VideoCourse.course_id == course.id,
            VideoCourse.video_id == video_id
        ).delete()
        db.session.commit()

    # resequence the remaining videos in the course
    vc_reorder = VideoCourse.query.filter(
        VideoCourse.course_id == course.id,
        VideoCourse.video_seq > video_seq
    )

    for vc in vc_reorder:
        vc.video_seq = vc.video_seq - 1

    # re-render the course edit page without the removed video
    return redirect(f'../../../courses/{course_id}/edit')

# ************************************
# OTHER ROUTES
# ************************************

@app.errorhandler(404)
def page_not_found(error):
    """Handle 404 errors by showing custom 404 page."""

    return render_template('404.html'), 404


@app.route("/")
def homepage():
    """Show homepage.

    - anon users: no courses
    - logged in: button to navigate to page to search for courses
    """
    if g.user:
        return render_template('home.html')
    else:
        return render_template('home-anon.html')


## ************************************************
## HELPER FUNCTIONS - Flask API search for videos
## ************************************************

def get_form_data():
    """Get search data from client form."""

    # get search form data from app.js
    data = {}
    data["keyword"] = request.json['keyword']

    return data


def validate_data(data):
    """Check for missing data from client."""

    errors = {'errors': {}}

    # if keyword missing from form
    if not data['keyword']:
        keyword_err = ["This field is required."]
        errors['errors']['keyword'] = keyword_err

    return errors


def get_yt_videos(keyword):
    """Get videos from YouTube API on a given topic."""

    MAX_RESULTS = 20

    # search for video data
    search_json = yt_search(keyword, MAX_RESULTS)

    items = search_json["items"]

    # create list of dicts containing info & data re: individual videos
    videos_data = create_list_of_videos(items)

    res_json = jsonify(videos_data)

    return res_json


def yt_search(keyword, max_results):
    """Retrieve videos by keyword.
    Limit results to number in max_results.
    Return JSON response."""

    # search for video data
    res = requests.get(
        f"{API_BASE_URL}/search/?part=snippet&maxResults={max_results}&type=video&q={keyword}&order=relevance&key={API_SECRET_KEY}"
    )

    # turn search results into json
    res_json = res.json()

    return res_json


# create list of dicts containing info & data re: individual videos
def create_list_of_videos(items):

    videos_data = []

    for video in items:

        video_data = {}
        # add video data to video_data dict
        video_data["ytVideoId"] = video['id']['videoId']
        video_data["title"] = video['snippet']['title']
        video_data["channelId"] = video['snippet']['channelId']
        video_data["channelTitle"] = video['snippet']['channelTitle']
        video_data["description"] = video['snippet']['description']
        video_data["thumb_url_medium"] = video['snippet']['thumbnails']['high']['url']

        videos_data.append(video_data)

    return videos_data


def yt_videos(yt_video_id):
    """Make API call to YouTube Data API.
    Return the result in JSON format."""

    res = requests.get(
        f"{API_BASE_URL}/videos?part=player&id={yt_video_id}&key={API_SECRET_KEY}"
    )
    videos_json = res.json()

    return videos_json

## *********************************
## HELPER FUNCTION(S): video route(s)
## *********************************

def add_video_to_db(form_data, yt_video_id):
    """Add a video to the database."""

    # CHANGE: should .first() be .one_or_none instead?
    video = Video.query.filter(Video.yt_video_id == yt_video_id).first()

    if not video:
        # CHANGE: is there a more efficient way to do this?
        # get video info from hidden form fields
        # CHANGE: pull this out into a helper function
        title = form_data.get('v-title', None)
        description = form_data.get('v-description', None)
        channelId = form_data.get('v-channelId', None)
        channelTitle = form_data.get('v-channelTitle', None)
        thumb_url = form_data.get('v-thumb-url', None)

        # create new video
        # CHANGE: pull this out into a helper function
        video = Video(title=title,
                      description=description,
                      yt_video_id=yt_video_id,
                      yt_channel_id=channelId,
                      yt_channel_title=channelTitle,
                      thumb_url=thumb_url)

        # add new video to database
        db.session.add(video)
        db.session.commit()

    return video

# *******************************
# HELPER FUNCTIONS: user routes
# *******************************

def do_login(user):
    """Log in user."""
    session[CURR_USER_KEY] = user.id


def do_logout():
    """Logout user."""

    if CURR_USER_KEY in session:
        del session[CURR_USER_KEY]
        g.user = None

def signup_demo_user():
    """Create a demo account."""

    user = User.signup(
        username="Demo",
        password="demodemo",
        first_name="Demo",
        last_name="Demo",
        image_url=User.image_url.default.arg,
        email="demo@demo.com",
    )
    db.session.add(user)
    db.session.commit()

    return user

def populate_demo_data():
    """Make a course for the demo account and add videos to it."""

    # create a course owned by demo user
    course = Course(title="PMP Test Preparation",
                    description="Learn everything you need to know to pass the PMP on your first try.",
                    creator_id=g.user.id)
    db.session.add(course)
    db.session.commit()

    # add video1 to database and the course
    video1 = {"v-title": "PMP Exam Questions And Answers - PMP Certification- PMP Exam Prep (2020) - Video 1",
            "v-description": "Lot of people think that solving thousands of PMP exam questions and answers will be the deal breaker in there PMP exam prep program. I am not 100% ...",
            "yt_video_id": "slJRAbvvAr8",
            "v-channelId": "UCij4PbZVBmFbUYieXQmt6lQ",
            "v-channelTitle": "EduHubSpot",
            "v-thumb-url": "https://i.ytimg.com/vi/slJRAbvvAr8/hqdefault.jpg"}
    add_video_to_db(video1, video1["yt_video_id"])
    video1_db = Video.query.filter(Video.yt_video_id == video1["yt_video_id"]).first()
    
    video1_seq = len(course.videos) + 1
    video_course1 = VideoCourse(course_id=course.id,
                               video_id=video1_db.id,
                               video_seq=video1_seq)
    db.session.add(video_course1)
    db.session.commit()

    # add video2 to database and the course
    video2 = {"v-title": "PMP® Certification Full Course - Learn PMP Fundamentals in 12 Hours | PMP® Training Videos | Edureka",
            "v-description": "Edureka PMP® Certification Training: https://www.edureka.co/pmp-certification-exam-training This Edureka PMP® Certification Full Course video will help you ...",
            "yt_video_id": "vzqDTSZOTic",
            "v-channelId": "UCkw4JCwteGrDHIsyIIKo4tQ",
            "v-channelTitle": "edureka!",
            "v-thumb-url": "https://i.ytimg.com/vi/vzqDTSZOTic/hqdefault.jpg"}
    add_video_to_db(video2, video2["yt_video_id"])
    video2_db = Video.query.filter(Video.yt_video_id == video2["yt_video_id"]).first()

    video2_seq = len(course.videos) + 1
    video_course2 = VideoCourse(course_id=course.id,
                               video_id=video2_db.id,
                               video_seq=video2_seq)
    db.session.add(video_course2)
    db.session.commit()

    # add video3 to database and the course
    video3 = {"v-title": "PMP Exam Prep 25 What would you do next questions with Aileen",
            "v-description": "In this video, 25 what would you do next questions for the PMP Exam, Aileen reviews the strategy to address the many what would you do next questions on the ...",
            "yt_video_id": "MQ0f7WLYTlI",
            "v-channelId": "UCzl_4rhvVtjJ_rSIC1HRvmw",
            "v-channelTitle": "Aileen Ellis",
            "v-thumb-url": "https://i.ytimg.com/vi/MQ0f7WLYTlI/hqdefault.jpg"}
    add_video_to_db(video3, video3["yt_video_id"])
    video3_db = Video.query.filter(Video.yt_video_id == video3["yt_video_id"]).first()

    video3_seq = len(course.videos) + 1
    video_course3 = VideoCourse(course_id=course.id,
                               video_id=video3_db.id,
                               video_seq=video3_seq)
    db.session.add(video_course3)
    db.session.commit()