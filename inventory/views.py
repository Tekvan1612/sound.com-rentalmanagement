from datetime import datetime
import logging
import os
import json
from decimal import Decimal

import operation
from django.contrib import messages
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
import cloudinary
import cloudinary.api
import cloudinary.uploader
from psycopg2 import IntegrityError
from django.conf import settings
from django.core.paginator import Paginator
from django.db import connection, transaction

logger = logging.getLogger(__name__)


def dashboard(request):
    if not request.session.get('is_authenticated_custom'):
        return redirect('inventory:login')
    return render(request, 'inventory/dashboard.html')


def custom_login(request):
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()

        if not username or not password:
            messages.error(request, "Username and password are required.")
            return render(request, 'inventory/login.html')

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT user_exists, user_id FROM public.validate_user(%s, %s, %s)",
                    [username, password, True]
                )
                result = cursor.fetchone()

                if result and result[0]:
                    user_id = result[1]

                    cursor.execute("""
                                   SELECT mm.module_name
                                   FROM user_junction_module ujm
                                            JOIN module_master mm
                                                 ON ujm.module_id = mm.module_id
                                   WHERE ujm.user_id = %s
                                   """, [user_id])

                    modules = [row[0] for row in cursor.fetchall()]

                    request.session['username'] = username
                    request.session['user_id'] = user_id
                    request.session['modules'] = modules
                    request.session['is_authenticated_custom'] = True

                    redirect_url = reverse('inventory:dashboard')

                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return JsonResponse({
                            'status': 'success',
                            'redirect_url': redirect_url
                        })

                    return redirect(redirect_url)

                messages.error(request, "Invalid login details.")

        except Exception as e:
            messages.error(request, f"Login failed: {str(e)}")

    return render(request, 'inventory/login.html')


def logout_view(request):
    request.session.flush()
    return redirect('inventory:login')


def add_user(request):
    session_username = request.session.get('username')

    if request.method == 'POST':
        user_name = request.POST.get('username')
        emp_id = request.POST.get('emp_id')
        password = request.POST.get('password')
        status = request.POST.get('status') == '1'
        permissions = request.POST.get('permissions')
        created_by = int(request.session.get('user_id'))

        if not user_name:
            return JsonResponse({'success': False, 'message': 'Username is required.'})

        if not emp_id:
            return JsonResponse({'success': False, 'message': 'Employee is required.'})

        if not password:
            return JsonResponse({'success': False, 'message': 'Password is required.'})

        try:
            permissions = json.loads(permissions) if permissions else []
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'message': 'Invalid permissions data.'})

        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT manage_user(%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                        """,
                        [
                            'create',
                            None,
                            user_name,
                            password,
                            status,
                            json.dumps(permissions),
                            created_by,
                            int(emp_id)
                        ]
                    )
                    row = cursor.fetchone()
                    user_id = row[0] if row else None

            return JsonResponse({
                'success': True,
                'message': f'User {user_name} added successfully.',
                'user_id': user_id
            })

        except Exception as e:
            return JsonResponse({'success': False, 'message': f'Error occurred: {str(e)}'})

    with connection.cursor() as cursor:
        cursor.execute("SELECT id, name FROM employee ORDER BY name")
        employees = cursor.fetchall()

    employee_data = [{'id': row[0], 'name': row[1]} for row in employees]

    return render(
        request,
        'inventory/User.html',
        {
            'employee_data': employee_data,
            'username': session_username
        }
    )


def user_list(request):
    user_listing = []
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM getuser()")
            rows = cursor.fetchall()

            for row in rows:
                created_date_time = row[6].strftime('%d-%m-%Y')
                user_listing.append({
                    'user_id': row[0],
                    'user_name': row[1],
                    'password': row[2],
                    'status': row[3],
                    'modules': row[4] if row[4] else [],  # Ensure modules is a list
                    'created_by': row[5],
                    'created_date_time': created_date_time
                })
    except Exception as e:
        print("Error fetching user list:", e)

    # Implement pagination
    page = request.GET.get('page', 1)
    page_size = request.GET.get('page_size', 10)
    paginator = Paginator(user_listing, page_size)
    page_obj = paginator.get_page(page)

    response = {
        'data': list(page_obj.object_list),
        'total_items': paginator.count,
        'total_pages': paginator.num_pages,
        'current_page': page_obj.number,
    }

    return JsonResponse(response)

def update_user(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method'}, status=405)

    user_id = request.POST.get('userId')
    user_name = request.POST.get('username')
    password = request.POST.get('password')
    status = request.POST.get('status') == '1'
    emp_id = request.POST.get('emp_id')
    permissions = request.POST.get('permissions')

    try:
        permissions = json.loads(permissions) if permissions else []
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid permissions data'}, status=400)

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT manage_user(%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                """,
                [
                    'update',
                    int(user_id),
                    user_name,
                    password,
                    status,
                    json.dumps(permissions),
                    None,
                    int(emp_id)
                ]
            )

        return JsonResponse({'message': 'User details updated successfully', 'user_id': user_id})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


def delete_user(request, id):
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method'}, status=405)

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT manage_user(%s, %s, %s, %s, %s, %s::jsonb, %s, %s)",
                ['delete', int(id), None, None, None, None, None, None]
            )

        return JsonResponse({'message': 'User deleted successfully', 'user_id': id})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


# Employees Module
def add_employee(request):
    username = request.session.get('username')
    if request.method == 'POST':
        try:
            # Log data for debugging
            print("Received POST data:", request.POST)
            print("Received FILES data:", request.FILES)

            # Validate and extract data
            employee_id = int(request.POST.get('employee_id').strip())
            name = request.POST.get('name')
            email = request.POST.get('email')
            designation = request.POST.get('designation')
            mobile_no = int(request.POST.get('mobile_no').strip())
            gender = request.POST.get('gender')
            joining_date = datetime.strptime(request.POST.get('joining_date'), '%Y-%m-%d').date()
            dob = datetime.strptime(request.POST.get('dob'), '%Y-%m-%d').date()
            reporting_id = request.POST.get('reporting')
            p_address = request.POST.get('p_address')
            c_address = request.POST.get('c_address')
            country = request.POST.get('country')
            state = request.POST.get('state')
            status = request.POST.get('status').lower() == 'true'
            blood_group = request.POST.get('bloodGroup')
            created_by = request.session.get('user_id')
            created_date = datetime.now().replace(tzinfo=None)  # timestamp without timezone
            profile_photo = request.FILES.get('profile_photo')
            attachment_images = request.FILES.getlist('attachments[]')

            # Cloudinary upload for profile photo
            profile_photo_url = None
            if profile_photo:
                min_size = 5 * 1024  # 5 KB
                max_size = 5 * 1024 * 1024  # 5 MB

                if profile_photo:
                    max_size = 5 * 1024 * 1024  # 5 MB
                    if profile_photo.size > max_size:
                        return JsonResponse(
                            {'error': 'Profile photo size must not exceed 5MB.'},
                            status=400
                        )

            # Cloudinary upload for attachments
            image_urls = []
            for image in attachment_images[:2]:  # Limiting to first 2 attachments
                if image:
                    upload_result = cloudinary.uploader.upload(image, folder="uploads/")
                    image_urls.append(upload_result['secure_url'])  # Get the URL of the uploaded image

            # Ensure there are at least two entries in image_urls to avoid index errors
            while len(image_urls) < 2:
                image_urls.append(None)

            # Check for duplicates
            with connection.cursor() as cursor:
                cursor.execute("""
                               SELECT COUNT(*)
                               FROM employee
                               WHERE employee_id = %s
                                  OR email = %s
                                  OR mobile_no = %s
                               """, [employee_id, email, mobile_no])
                duplicate_count = cursor.fetchone()[0]

            if duplicate_count > 0:
                return JsonResponse({'error': 'Employee with this ID, email, or mobile number already exists.'},
                                    status=400)

            # Fetch reporting name
            with connection.cursor() as cursor:
                cursor.execute("SELECT name FROM employee WHERE id = %s", [reporting_id])
                reporting_name = cursor.fetchone()
                if reporting_name is None:
                    return JsonResponse({'error': 'Invalid reporting ID.'}, status=400)
                reporting_name = reporting_name[0]

            # Call stored procedure
            try:
                with connection.cursor() as cursor:
                    cursor.callproc('add_employee', [
                        employee_id, name, email, designation, mobile_no, gender,
                        joining_date, dob, reporting_name, p_address, c_address, country, state,
                        status, blood_group, created_by, created_date,
                        profile_photo_url, image_urls[0], image_urls[1]
                    ])
            except IntegrityError as e:
                return JsonResponse({'error': 'Integrity error occurred: ' + str(e)}, status=400)

            return JsonResponse({'success': 'Employee added successfully'}, status=200)

        except Exception as e:
            print(f"An unexpected error occurred: {str(e)}")
            return JsonResponse({'error': 'An unexpected error occurred: ' + str(e)}, status=500)

    return render(request, 'inventory/Employee_master.html', {'employees': get_all_employees(), 'username': username})


def get_all_employees():
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, name FROM employee")
        employees = [{'id': row[0], 'name': row[1]} for row in cursor.fetchall()]
    return employees


def employee_dropdown(request):
    search_query = request.GET.get('query', '')  # Capture search query from frontend
    with connection.cursor() as cursor:
        if search_query:
            cursor.execute("SELECT id, name FROM employee WHERE name ILIKE %s", ['%' + search_query + '%'])
        else:
            cursor.execute("SELECT id, name FROM employee")
        employees = cursor.fetchall()
        employee_list = [{'id': emp[0], 'name': emp[1]} for emp in employees]

    return JsonResponse({'employees': employee_list})


def employee_list(request):
    employee_listing = []
    try:
        with connection.cursor() as cursor:
            # Fetch employee details
            cursor.execute("SELECT * FROM get_employee_details()")
            rows = cursor.fetchall()
            # print('Check the employee Details:', rows)

            for index, row in enumerate(rows):
                # print('Check the for loop')
                # Log row structure for debugging
                logger.debug(f"Row data: {row}")

                # Extract each field from the row
                employee_id = row[1]
                name = row[2]
                email = row[3]
                designation = row[4]
                mobile_no = row[5]
                gender = row[6]
                joining_date = row[7].strftime('%Y-%m-%d') if row[7] else None
                dob = row[8].strftime('%Y-%m-%d') if row[8] else None
                reporting_name = row[9]
                p_address = row[10]
                c_address = row[11]
                country = row[12]
                state = row[13]
                status = row[14]
                blood_group = row[15]
                created_by = row[16]  # Assuming this is created_by, adjust as necessary
                created_date = row[17].strftime('%Y-%m-%d') if row[17] else None  # Format created_date
                profile_pic = row[18]  # Profile picture path
                attachments = row[19] or []  # Attachments array
                # print('Fetch the Form DATA:', employee_id,name,email, designation,mobile_no, gender, joining_date, dob, reporting_name, p_address, c_address, country,
                # profile_pic, attachments)

                # Handle profile picture URL
                if profile_pic:
                    # If the profile_pic already contains a full URL (e.g., Cloudinary URL)
                    if profile_pic.startswith('http://') or profile_pic.startswith('https://'):
                        image_url = profile_pic  # Use the URL as is
                    else:
                        # Otherwise, assume it's a local file path and construct the media URL
                        image_url = f'{settings.MEDIA_URL}{profile_pic}'.replace('\\', '/')
                else:
                    # Fallback to default profile picture if none is provided
                    image_url = f'{settings.MEDIA_URL}profilepic/default.jpg'

                # Handle attachments (array of images)
                attachment_urls = []
                for attachment in attachments:
                    if attachment:
                        attachment_url = os.path.join(settings.MEDIA_URL, attachment).replace('\\', '/')
                        attachment_urls.append(attachment_url)

                # Add employee details to the list
                employee_listing.append({
                    'sr_no': index + 1,
                    'id': row[0],
                    'employee_id': employee_id,
                    'name': name,
                    'email': email,
                    'mobile_no': mobile_no,
                    'designation': designation,
                    'gender': gender,
                    'joining_date': joining_date,
                    'dob': dob,
                    'reporting': reporting_name,
                    'p_address': p_address,
                    'c_address': c_address,
                    'country': country,
                    'state': state,
                    'status': status,
                    'blood_group': blood_group,
                    'created_by': created_by,
                    'created_date': created_date,
                    'profile_pic': image_url,
                    'attachments': attachment_urls  # List of attachment URLs
                })
            # print('Check the Employee Listing:', employee_listing)

    except Exception as e:
        logger.error("An error occurred while fetching the employee list: %s", str(e), exc_info=True)
        return JsonResponse({'error': 'An error occurred while fetching the employee list: ' + str(e)}, status=500)

    # Pagination
    page = int(request.GET.get('page', 1))
    page_size = int(request.GET.get('page_size', 10))
    paginator = Paginator(employee_listing, page_size)
    page_obj = paginator.get_page(page)

    response = {
        'data': list(page_obj.object_list),
        'total_items': paginator.count,
        'total_pages': paginator.num_pages,
        'current_page': page_obj.number,
    }

    return JsonResponse(response)


@csrf_exempt
def delete_attachment(request):
    if request.method == 'POST':
        attachment_id = request.POST.get('attachment_id')

        if attachment_id:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM employee_images WHERE id = %s", [attachment_id])
                return JsonResponse({'success': 'Attachment deleted successfully'})
            except Exception as e:
                return JsonResponse({'error': 'Error deleting attachment: ' + str(e)}, status=400)
        else:
            return JsonResponse({'error': 'Invalid attachment ID'}, status=400)
    return JsonResponse({'error': 'Invalid request'}, status=400)


def modify_employee(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request'}, status=400)

    operation = request.POST.get('operation')
    emp_id = request.POST.get('id')

    if not emp_id or not emp_id.isdigit():
        return JsonResponse({'error': 'Invalid employee ID'}, status=400)

    emp_id = int(emp_id)

    try:
        # ---------------- DELETE ----------------
        if operation == 'delete':
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT modify_employee(%s, %s)",
                    ['delete', emp_id]
                )
            return JsonResponse({'success': 'Employee deleted successfully'})

        # ---------------- UPDATE ----------------
        elif operation == 'update':
            emp_employee_id = request.POST.get('emp_id') or None
            emp_name = request.POST.get('name') or None
            emp_email = request.POST.get('email') or None
            emp_designation = request.POST.get('designation') or None
            emp_mobile_no = request.POST.get('mobile_no') or None
            emp_gender = request.POST.get('gender') or None
            emp_joining_date = request.POST.get('joining_date') or None
            emp_dob = request.POST.get('dob') or None
            emp_reporting = request.POST.get('reporting') or None
            emp_p_address = request.POST.get('p_address') or None
            emp_c_address = request.POST.get('c_address') or None
            emp_country = request.POST.get('country') or None
            emp_state = request.POST.get('state') or None
            emp_status = request.POST.get('status') == 'true'
            emp_blood_group = request.POST.get('bloodGroup') or None
            removed_profile_pic = request.POST.get('removed_profile_pic') == 'true'

            removed_attachments = request.POST.get('removed_attachments')
            removed_attachments = json.loads(removed_attachments) if removed_attachments else []

            with transaction.atomic():

                # -------- Update Employee --------
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT modify_employee(
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        """,
                        [
                            'update',
                            emp_id,
                            int(emp_employee_id) if emp_employee_id else None,
                            emp_name,
                            emp_email,
                            emp_designation,
                            int(emp_mobile_no) if emp_mobile_no else None,
                            emp_gender,
                            emp_joining_date,
                            emp_dob,
                            emp_reporting,
                            emp_p_address,
                            emp_c_address,
                            emp_country,
                            emp_state,
                            emp_status,
                            emp_blood_group
                        ]
                    )

                # -------- Profile Photo Upload --------
                profile_photo = request.FILES.get('profile_photo')
                if profile_photo:
                    upload_result = cloudinary.uploader.upload(profile_photo, folder="profilepic/")
                    profile_pic_url = upload_result['secure_url']

                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT id FROM employee_images WHERE employee_id = %s AND images LIKE %s",
                            [emp_id, '%profilepic%']
                        )
                        existing_image = cursor.fetchone()

                        if existing_image:
                            cursor.execute(
                                "UPDATE employee_images SET images = %s WHERE employee_id = %s AND images LIKE %s",
                                [profile_pic_url, emp_id, '%profilepic%']
                            )
                        else:
                            cursor.execute(
                                "INSERT INTO employee_images (employee_id, images) VALUES (%s, %s)",
                                [emp_id, profile_pic_url]
                            )

                # -------- Attachments Upload --------
                attachments = request.FILES.getlist('attachments')
                for attachment in attachments:
                    upload_result = cloudinary.uploader.upload(attachment, folder="uploads/")
                    attachment_url = upload_result['secure_url']

                    with connection.cursor() as cursor:
                        cursor.execute(
                            "INSERT INTO employee_images (employee_id, images) VALUES (%s, %s)",
                            [emp_id, attachment_url]
                        )

                # -------- Remove Profile Photo --------
                if removed_profile_pic:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "DELETE FROM employee_images WHERE employee_id = %s AND images LIKE %s",
                            [emp_id, '%profilepic%']
                        )

                # -------- Remove Attachments --------
                for attachment_id in removed_attachments:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "DELETE FROM employee_images WHERE id = %s",
                            [attachment_id]
                        )

            return JsonResponse({'success': 'Employee updated successfully'})

        # ---------------- INVALID ----------------
        else:
            return JsonResponse({'error': 'Invalid operation'}, status=400)

    except Exception as e:
        logger.error("Error in modify_employee: %s", str(e))
        return JsonResponse({'error': str(e)}, status=400)


def asset_entry(request):
    if not request.session.get('is_authenticated_custom'):
        return redirect('inventory:login')

    username = request.session.get('username')

    equipment_listing = []
    subcategories = []

    def normalize_image(image_path):
        if not image_path:
            return ''

        image_path = str(image_path).replace("\\", "/")

        if image_path.startswith("http://") or image_path.startswith("https://"):
            return image_path

        media_url = settings.MEDIA_URL.rstrip("/") + "/"

        if image_path.startswith(media_url):
            return image_path

        if "/media/" in image_path:
            return image_path[image_path.index("/media/"):]

        return f"{media_url}attachments/{os.path.basename(image_path)}"

    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    el.id,
                    el.equipment_name,
                    sc.name AS sub_category_name,
                    el.category_type,
                    um.user_name AS created_by,
                    el.created_date,
                    el.dimension_height,
                    el.dimension_width,
                    el.dimension_length,
                    el.hsn_no,
                    ela.image_1,
                    ela.image_2,
                    ela.image_3
                FROM public.equipment_list el
                LEFT JOIN public.sub_category sc
                    ON el.sub_category_id = sc.id
                LEFT JOIN public.user_master um
                    ON el.created_by = um.user_id
                LEFT JOIN public.equipment_list_attachments ela
                    ON ela.equipment_list_id = el.id
                ORDER BY el.id DESC
            """)
            rows = cursor.fetchall()

            for row in rows:
                created_date = row[5].strftime('%d-%m-%Y') if row[5] else ''

                equipment_listing.append({
                    'id': row[0],
                    'equipment_name': row[1] or '',
                    'sub_category_name': row[2] or '',
                    'category_type': row[3] or '',
                    'created_by': row[4] or '',
                    'created_date': created_date,
                    'dimension_height': row[6] or '',
                    'dimension_width': row[7] or '',
                    'dimension_length': row[8] or '',
                    'hsn_no': row[9] or '',
                    'image_1': normalize_image(row[10]),
                    'image_2': normalize_image(row[11]),
                    'image_3': normalize_image(row[12]),
                })

            cursor.execute("""
                SELECT id, category_name, name
                FROM get_sub()
            """)
            subcategories = [
                {
                    'id': row[0],
                    'category_name': row[1],
                    'name': row[2]
                }
                for row in cursor.fetchall()
            ]

    except Exception as e:
        print("ERROR in asset_entry:", e)

    context = {
        'equipment_listing': equipment_listing,
        'subcategories': subcategories,
        'username': username
    }

    return render(request, 'inventory/asset_creation.html', context)


# Master Category Module
def add_category(request):
    if request.method == 'POST':
        category_name = request.POST.get('category_name', '').upper()
        description = request.POST.get('description', '')
        status = request.POST.get('status') == '1'
        created_by = request.session.get('user_id')
        created_date = datetime.now()

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM master_category WHERE category_name = %s",
                    [category_name]
                )
                category_count = cursor.fetchone()[0]

                if category_count > 0:
                    return JsonResponse({'success': False, 'message': 'Category Already Exists!'})

                cursor.execute(
                    "SELECT add_category(%s, %s, %s, %s, %s);",
                    [category_name, description, status, created_by, created_date]
                )

            return JsonResponse({'success': True, 'message': 'Category added successfully!'})

        except Exception as e:
            return JsonResponse({
                'success': False,
                'message': f'An unexpected error occurred: {str(e)}'
            })

    return JsonResponse({'success': False, 'message': 'Invalid request method'})


def category_list(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                           SELECT mc.category_id,
                                  mc.category_name,
                                  mc.category_description,
                                  um.user_name AS created_by,
                                  mc.created_date,
                                  mc.status
                           FROM public.master_category mc
                                    JOIN public.user_master um
                                         ON mc.created_by = um.user_id
                           ORDER BY mc.category_name
                           """)

            rows = cursor.fetchall()

            categories = [
                {
                    'id': row[0],
                    'category_name': row[1],
                    'description': row[2],
                    'created_by': row[3],
                    'created_date': row[4].strftime('%d-%m-%Y') if row[4] else None,
                    'status': row[5]
                }
                for row in rows
            ]

            return JsonResponse({'categories': categories})

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
def update_category(request, category_id):
    if request.method == 'POST':

        category_name = request.POST.get('categoryName', '').upper()
        category_description = request.POST.get('categoryDescription', '')
        status = request.POST.get('statusText') in ['true', 'True', '1']

        try:
            with connection.cursor() as cursor:

                cursor.callproc(
                    'update_category',
                    [category_id, category_name, category_description, status]
                )

                updated_category_id = cursor.fetchone()[0]

            return JsonResponse({
                'success': True,
                'message': 'Category details updated successfully',
                'updated_category_id': updated_category_id
            })

        except Exception as e:

            return JsonResponse({
                'success': False,
                'message': 'Failed to update category details',
                'exception': str(e)
            })

    return JsonResponse({
        'success': False,
        'message': 'Invalid request method'
    })


@csrf_exempt
def delete_category(request, category_id):
    if request.method != "POST":
        return JsonResponse({
            "success": False,
            "message": "Invalid request method"
        })

    try:
        with connection.cursor() as cursor:
            # 1) Check if any equipment exists under subcategories of this category
            cursor.execute("""
                           SELECT COUNT(*)
                           FROM public.equipment_list el
                                    JOIN public.sub_category sc
                                         ON el.sub_category_id = sc.id
                           WHERE sc.category_id = %s
                           """, [category_id])
            equipment_count = cursor.fetchone()[0]

            if equipment_count > 0:
                return JsonResponse({
                    "success": False,
                    "message": "Cannot delete category. Equipment exists under this category."
                })

            # 2) Check if any subcategory exists under this category
            cursor.execute("""
                           SELECT COUNT(*)
                           FROM public.sub_category
                           WHERE category_id = %s
                           """, [category_id])
            subcategory_count = cursor.fetchone()[0]

            if subcategory_count > 0:
                return JsonResponse({
                    "success": False,
                    "message": "Cannot delete category. Subcategories exist under this category."
                })

            # 3) Safe to delete
            cursor.execute("""
                           DELETE
                           FROM public.master_category
                           WHERE category_id = %s
                           """, [category_id])

        return JsonResponse({
            "success": True,
            "message": "Category deleted successfully"
        })

    except Exception as e:
        return JsonResponse({
            "success": False,
            "message": str(e)
        })


def category_dropdown(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT category_id, category_name FROM get_category_details()')
            categories = [{'id': row[0], 'name': row[1]} for row in cursor.fetchall()]
            return JsonResponse({'categories': categories}, safe=False)
    except Exception as e:
        return JsonResponse({'categories': []})


def get_category_dropdown(request, category_id):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT category_id, category_name FROM master_category WHERE category_id = %s",
            [category_id]
        )
        category_data = cursor.fetchone()

    return JsonResponse({
        'category_id': category_data[0],
        'category_name': category_data[1]
    })


@csrf_exempt
def add_subcategory(request):
    if request.method == 'POST':
        category_id = request.POST.get('category_id')
        name = request.POST.get('name', '').strip()
        type_value = request.POST.get('type', '').strip()
        status = request.POST.get('status') == '1'
        created_by = request.session.get('user_id')

        if not created_by:
            return JsonResponse({
                'success': False,
                'message': 'User session expired. Please login again.'
            })

        if not category_id:
            return JsonResponse({
                'success': False,
                'message': 'Master category is required.'
            })

        if not name:
            return JsonResponse({
                'success': False,
                'message': 'Subcategory name is required.'
            })

        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                               SELECT *
                               FROM public.manage_subcategory(
                                       %s, %s, %s, %s, %s, %s, %s
                                    )
                               """, ['ADD', None, category_id, name, type_value, status, created_by])

                row = cursor.fetchone()

            return JsonResponse({
                'success': row[0],
                'message': row[1],
                'id': row[2]
            })

        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})

    return JsonResponse({'success': False, 'message': 'Invalid request method'})


# Sub Category Module
def subcategory_list(request, category_id=None):
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                           SELECT *
                           FROM public.manage_subcategory(
                                   %s, %s, %s, %s, %s, %s, %s
                                )
                           """, ['LIST', None, category_id, None, None, None, None])

            rows = cursor.fetchall()

        subcategories = [
            {
                'success': row[0],
                'message': row[1],
                'id': row[2],
                'category_id': row[3],
                'category_name': row[4],
                'name': row[5],
                'type': row[6],
                'status': row[7],
                'created_by': row[8],
                'created_date': row[9]
            }
            for row in rows if row[2] is not None
        ]

        return JsonResponse({'subcategories': subcategories})

    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)})


@csrf_exempt
def update_subcategory(request, id):
    if request.method == 'POST':
        category_id = request.POST.get('category_id')
        name = request.POST.get('name', '')
        type_value = request.POST.get('type', '')
        status = request.POST.get('status') in ['1', 'true', 'True']

        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                               SELECT *
                               FROM public.manage_subcategory(
                                       %s, %s, %s, %s, %s, %s, %s
                                    )
                               """, ['UPDATE', id, category_id, name, type_value, status, None])

                row = cursor.fetchone()

            return JsonResponse({
                'success': row[0],
                'message': row[1],
                'updated_subcategory_id': row[2]
            })

        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})

    return JsonResponse({'success': False, 'message': 'Invalid request method'})


@csrf_exempt
def delete_subcategory(request, id):
    if request.method == 'POST':
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                               SELECT *
                               FROM public.manage_subcategory(
                                       %s, %s, %s, %s, %s, %s, %s
                                    )
                               """, ['DELETE', id, None, None, None, None, None])

                row = cursor.fetchone()

            return JsonResponse({
                'success': row[0],
                'message': row[1]
            })

        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})

    return JsonResponse({'success': False, 'message': 'Invalid request method'})


def save_uploaded_file(file_obj):
    if not file_obj:
        return None

    attachment_dir = os.path.join(settings.MEDIA_ROOT, 'attachments')
    os.makedirs(attachment_dir, exist_ok=True)

    file_path = os.path.join(attachment_dir, file_obj.name)
    with open(file_path, 'wb') as f:
        for chunk in file_obj.chunks():
            f.write(chunk)

    return file_path


def add_equipment(request):
    if request.method != 'POST':
        return JsonResponse(
            {'success': False, 'message': 'Invalid request method.'},
            status=405
        )

    try:
        equipment_name = request.POST.get('equipment_name', '').strip().upper()
        subcategory_id = request.POST.get('subcategory_id', '').strip()
        category_name = request.POST.get('category_name', '').strip().upper()
        type_value = request.POST.get('type', '').strip()
        dimension_h = request.POST.get('dimension_h', '').strip()
        dimension_w = request.POST.get('dimension_w', '').strip()
        dimension_l = request.POST.get('dimension_l', '').strip()
        weight = request.POST.get('weight', '').strip()
        volume = request.POST.get('volume', '').strip()
        hsn_no = request.POST.get('hsn_no', '').strip()
        country_origin = request.POST.get('country_origin', '').strip()
        status_raw = request.POST.get('status', 'Active').strip()
        created_by = request.session.get('user_id')

        attachment_1 = request.FILES.get('attachment_1')
        attachment_2 = request.FILES.get('attachment_2')
        attachment_3 = request.FILES.get('attachment_3')

        status_value = True if status_raw == 'Active' else False

        if not equipment_name:
            return JsonResponse(
                {'success': False, 'message': 'Equipment name is required.'},
                status=400
            )

        if not subcategory_id:
            return JsonResponse(
                {'success': False, 'message': 'Subcategory is required.'},
                status=400
            )

        if not category_name:
            return JsonResponse(
                {'success': False, 'message': 'Category is required.'},
                status=400
            )

        if not created_by:
            return JsonResponse(
                {'success': False, 'message': 'Session expired. Please login again.'},
                status=400
            )

        try:
            subcategory_id = int(subcategory_id)
        except ValueError:
            return JsonResponse(
                {'success': False, 'message': 'Invalid subcategory id.'},
                status=400
            )

        try:
            dimension_h = Decimal(dimension_h) if dimension_h else None
            dimension_w = Decimal(dimension_w) if dimension_w else None
            dimension_l = Decimal(dimension_l) if dimension_l else None
            weight = Decimal(weight) if weight else None
            volume = Decimal(volume) if volume else None
        except Exception:
            return JsonResponse(
                {
                    'success': False,
                    'message': 'Height, width, length, weight, and volume must be numeric.'
                },
                status=400
            )

        # Duplicate check
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT 1
                FROM public.equipment_list
                WHERE UPPER(equipment_name) = %s
                  AND sub_category_id = %s
                LIMIT 1
            """, [equipment_name, subcategory_id])

            if cursor.fetchone():
                return JsonResponse(
                    {
                        'success': False,
                        'message': 'This equipment already exists in the selected subcategory.'
                    },
                    status=400
                )

        # Get subcategory name because SQL function expects subcategory name
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT name
                FROM public.sub_category
                WHERE id = %s
            """, [subcategory_id])
            row = cursor.fetchone()

        if not row:
            return JsonResponse(
                {'success': False, 'message': 'Selected subcategory not found.'},
                status=400
            )

        sub_category_name = row[0]

        image_1_path = save_uploaded_file(attachment_1)
        image_2_path = save_uploaded_file(attachment_2)
        image_3_path = save_uploaded_file(attachment_3)

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT public.insert_equipment_with_attachments(
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                );
            """, [
                equipment_name,
                sub_category_name,
                category_name,
                dimension_h,
                dimension_w,
                dimension_l,
                weight,
                volume,
                hsn_no or None,
                country_origin or None,
                created_by,
                image_1_path,
                image_2_path,
                image_3_path,
                status_value
            ])

            equipment_id = cursor.fetchone()[0]

        return JsonResponse({
            'success': True,
            'message': 'Equipment added successfully.',
            'equipment_id': equipment_id
        })

    except Exception as e:
        error_message = str(e)
        print("ADD_EQUIPMENT ERROR:", error_message)

        if 'equipment_list_name_subcategory_unique' in error_message:
            return JsonResponse(
                {
                    'success': False,
                    'message': 'This equipment already exists in the selected subcategory.'
                },
                status=400
            )

        if 'equipment_list_name_unique' in error_message:
            return JsonResponse(
                {
                    'success': False,
                    'message': 'This equipment already exists.'
                },
                status=400
            )

        return JsonResponse(
            {'success': False, 'message': error_message},
            status=500
        )

def insert_vendor(request):
    if request.method != 'POST':
        return JsonResponse({
            'success': False,
            'message': 'Invalid request method.'
        }, status=405)

    try:
        vendor_name = request.POST.get('vendor_name', '').strip()
        purchase_date = request.POST.get('purchase_date', '').strip()
        unit_price = request.POST.get('unit_price', '').strip()
        rental_price = request.POST.get('rental_price', '').strip()
        reference_no = request.POST.get('reference_no', '').strip() or None
        unit = request.POST.get('unitValue', '').strip()
        equipment_id = request.POST.get('equipmentId', '').strip()
        attachment = request.FILES.get('attachment')

        created_by = request.session.get('user_id')
        created_date = datetime.now()

        if not equipment_id:
            return JsonResponse({
                'success': False,
                'message': 'Equipment ID is missing.'
            }, status=400)

        if not vendor_name:
            return JsonResponse({
                'success': False,
                'message': 'Vendor name is required.'
            }, status=400)

        if not purchase_date:
            return JsonResponse({
                'success': False,
                'message': 'Purchase date is required.'
            }, status=400)

        if not unit:
            return JsonResponse({
                'success': False,
                'message': 'Unit is required.'
            }, status=400)

        if not created_by:
            return JsonResponse({
                'success': False,
                'message': 'Session expired. Please login again.'
            }, status=400)

        try:
            equipment_id = int(equipment_id)
        except ValueError:
            return JsonResponse({
                'success': False,
                'message': 'Invalid equipment ID.'
            }, status=400)

        try:
            unit = int(unit)
            if unit <= 0:
                raise ValueError
        except ValueError:
            return JsonResponse({
                'success': False,
                'message': 'Unit must be greater than 0.'
            }, status=400)

        try:
            unit_price = Decimal(unit_price) if unit_price else None
        except Exception:
            return JsonResponse({
                'success': False,
                'message': 'Invalid unit price.'
            }, status=400)

        try:
            rental_price = Decimal(rental_price) if rental_price else None
        except Exception:
            return JsonResponse({
                'success': False,
                'message': 'Invalid rental price.'
            }, status=400)

        serial_numbers = []
        barcode_numbers = []

        seen_serials = set()
        seen_barcodes = set()

        for i in range(1, unit + 1):
            serial_number = request.POST.get(f'serialNumber{i}', '').strip()
            barcode_number = request.POST.get(f'barcodeNumber{i}', '').strip()

            if not serial_number:
                return JsonResponse({
                    'success': False,
                    'message': f'Serial number is required for unit {i}.'
                }, status=400)

            if not barcode_number:
                return JsonResponse({
                    'success': False,
                    'message': f'Barcode number is required for unit {i}.'
                }, status=400)

            if serial_number in seen_serials:
                return JsonResponse({
                    'success': False,
                    'message': f'Duplicate serial number "{serial_number}" in this form.'
                }, status=400)

            if barcode_number in seen_barcodes:
                return JsonResponse({
                    'success': False,
                    'message': f'Duplicate barcode number "{barcode_number}" in this form.'
                }, status=400)

            seen_serials.add(serial_number)
            seen_barcodes.add(barcode_number)

            serial_numbers.append(serial_number)
            barcode_numbers.append(barcode_number)

        # Check duplicates already existing in DB
        with connection.cursor() as cursor:
            for serial_number in serial_numbers:
                cursor.execute("""
                    SELECT 1
                    FROM public.stock_details
                    WHERE serial_no = %s
                    LIMIT 1
                """, [serial_number])

                if cursor.fetchone():
                    return JsonResponse({
                        'success': False,
                        'message': f'Serial number "{serial_number}" already exists.'
                    }, status=400)

            for barcode_number in barcode_numbers:
                cursor.execute("""
                    SELECT 1
                    FROM public.stock_details
                    WHERE barcode_no = %s
                    LIMIT 1
                """, [barcode_number])

                if cursor.fetchone():
                    return JsonResponse({
                        'success': False,
                        'message': f'Barcode number "{barcode_number}" already exists.'
                    }, status=400)

        attachment_path = None
        if attachment:
            attachment_dir = os.path.join(settings.MEDIA_ROOT, 'attachments')
            os.makedirs(attachment_dir, exist_ok=True)

            attachment_path = os.path.join(attachment_dir, attachment.name)
            with open(attachment_path, 'wb') as f:
                for chunk in attachment.chunks():
                    f.write(chunk)

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT public.add_stock(
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                );
            """, [
                equipment_id,
                vendor_name,
                purchase_date,
                unit_price,
                rental_price,
                reference_no,
                attachment_path,
                unit,
                serial_numbers,
                barcode_numbers,
                created_by,
                created_date,
                None
            ])

        return JsonResponse({
            'success': True,
            'message': 'Stock details added successfully.'
        })

    except Exception as e:
        error_message = str(e)
        print("INSERT_VENDOR ERROR:", error_message)

        if 'stock_details_serial_no_unique' in error_message:
            return JsonResponse({
                'success': False,
                'message': 'Duplicate serial number is not allowed.'
            }, status=400)

        if 'stock_details_barcode_no_unique' in error_message:
            return JsonResponse({
                'success': False,
                'message': 'Duplicate barcode number is not allowed.'
            }, status=400)

        if 'stock_details_serial_no_not_blank' in error_message:
            return JsonResponse({
                'success': False,
                'message': 'Serial number cannot be blank.'
            }, status=400)

        if 'stock_details_barcode_no_not_blank' in error_message:
            return JsonResponse({
                'success': False,
                'message': 'Barcode number cannot be blank.'
            }, status=400)

        return JsonResponse({
            'success': False,
            'message': error_message
        }, status=500)

def equipment_stock_details(request, equipment_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT id, serial_no, barcode_no
                FROM public.stock_details
                WHERE equipment_id = %s
                ORDER BY id
            """, [equipment_id])

            rows = cursor.fetchall()

        stock_rows = []
        for row in rows:
            stock_rows.append({
                'id': row[0],
                'serial_no': row[1],
                'barcode_no': row[2],
            })

        return JsonResponse({
            'success': True,
            'stock_details': stock_rows
        })

    except Exception as e:
        print("EQUIPMENT_STOCK_DETAILS ERROR:", str(e))
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=500)

def update_stock_inline(request, row_id):
    if request.method != "POST":
        return JsonResponse({
            "success": False,
            "message": "Invalid request method."
        }, status=405)

    try:
        serial_no = request.POST.get("serial_no", "").strip()
        barcode_no = request.POST.get("barcode_no", "").strip()

        if not serial_no:
            return JsonResponse({
                "success": False,
                "message": "Serial number is required."
            }, status=400)

        if not barcode_no:
            return JsonResponse({
                "success": False,
                "message": "Barcode number is required."
            }, status=400)

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT 1
                FROM public.stock_details
                WHERE serial_no = %s AND id <> %s
                LIMIT 1
            """, [serial_no, row_id])
            if cursor.fetchone():
                return JsonResponse({
                    "success": False,
                    "message": "Serial number already exists."
                }, status=400)

            cursor.execute("""
                SELECT 1
                FROM public.stock_details
                WHERE barcode_no = %s AND id <> %s
                LIMIT 1
            """, [barcode_no, row_id])
            if cursor.fetchone():
                return JsonResponse({
                    "success": False,
                    "message": "Barcode number already exists."
                }, status=400)

            cursor.execute("""
                UPDATE public.stock_details
                SET serial_no = %s,
                    barcode_no = %s
                WHERE id = %s
            """, [serial_no, barcode_no, row_id])

        return JsonResponse({
            "success": True,
            "message": "Stock updated successfully."
        })

    except Exception as e:
        print("UPDATE_STOCK_INLINE ERROR:", str(e))
        return JsonResponse({
            "success": False,
            "message": str(e)
        }, status=500)


def delete_stock_detail(request, row_id):
    if request.method != "POST":
        return JsonResponse({
            "success": False,
            "message": "Invalid request method."
        }, status=405)

    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                DELETE FROM public.stock_details
                WHERE id = %s
            """, [row_id])

        return JsonResponse({
            "success": True,
            "message": "Stock deleted successfully."
        })

    except Exception as e:
        print("DELETE_STOCK_DETAIL ERROR:", str(e))
        return JsonResponse({
            "success": False,
            "message": str(e)
        }, status=500)


def get_equipment_detail(request, equipment_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    el.id,
                    el.equipment_name,
                    el.sub_category_id,
                    sc.name AS subcategory_name,
                    el.category_type,
                    el.dimension_height,
                    el.dimension_width,
                    el.dimension_length,
                    el.weight,
                    el.volume,
                    el.hsn_no,
                    el.country_origin,
                    el.status,
                    ela.image_1,
                    ela.image_2,
                    ela.image_3
                FROM public.equipment_list el
                LEFT JOIN public.sub_category sc
                    ON el.sub_category_id = sc.id
                LEFT JOIN public.equipment_list_attachments ela
                    ON ela.equipment_list_id = el.id
                WHERE el.id = %s
            """, [equipment_id])

            row = cursor.fetchone()

        if not row:
            return JsonResponse({
                'success': False,
                'message': 'Equipment not found.'
            }, status=404)

        return JsonResponse({
            'success': True,
            'equipment': {
                'id': row[0],
                'equipment_name': row[1] or '',
                'subcategory_id': row[2],
                'subcategory_name': row[3] or '',
                'category_name': row[4] or '',
                'dimension_h': str(row[5]) if row[5] is not None else '',
                'dimension_w': str(row[6]) if row[6] is not None else '',
                'dimension_l': str(row[7]) if row[7] is not None else '',
                'weight': str(row[8]) if row[8] is not None else '',
                'volume': str(row[9]) if row[9] is not None else '',
                'hsn_no': row[10] or '',
                'country_origin': row[11] or '',
                'status': 'Active' if row[12] else 'Inactive',
                'image_1': row[13] or '',
                'image_2': row[14] or '',
                'image_3': row[15] or '',
            }
        })

    except Exception as e:
        print("GET_EQUIPMENT_DETAIL ERROR:", str(e))
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=500)


def update_equipment(request, equipment_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=405)

    try:
        equipment_name = request.POST.get('equipment_name', '').strip().upper()
        subcategory_id = request.POST.get('subcategory_id', '').strip()
        category_name = request.POST.get('category_name', '').strip().upper()
        dimension_h = request.POST.get('dimension_h', '').strip()
        dimension_w = request.POST.get('dimension_w', '').strip()
        dimension_l = request.POST.get('dimension_l', '').strip()
        weight = request.POST.get('weight', '').strip()
        volume = request.POST.get('volume', '').strip()
        hsn_no = request.POST.get('hsn_no', '').strip()
        country_origin = request.POST.get('country_origin', '').strip()
        status_raw = request.POST.get('status', '').strip()

        attachment_1 = request.FILES.get('attachment_1')
        attachment_2 = request.FILES.get('attachment_2')
        attachment_3 = request.FILES.get('attachment_3')

        if not equipment_name:
            return JsonResponse({'success': False, 'message': 'Equipment name is required.'}, status=400)

        if not subcategory_id:
            return JsonResponse({'success': False, 'message': 'Subcategory is required.'}, status=400)

        if not category_name:
            return JsonResponse({'success': False, 'message': 'Category is required.'}, status=400)

        try:
            subcategory_id = int(subcategory_id)
        except ValueError:
            return JsonResponse({'success': False, 'message': 'Invalid subcategory id.'}, status=400)

        try:
            dimension_h = Decimal(dimension_h) if dimension_h else None
            dimension_w = Decimal(dimension_w) if dimension_w else None
            dimension_l = Decimal(dimension_l) if dimension_l else None
            weight = Decimal(weight) if weight else None
            volume = Decimal(volume) if volume else None
        except Exception:
            return JsonResponse({
                'success': False,
                'message': 'Height, width, length, weight, and volume must be numeric.'
            }, status=400)

        status_value = True if status_raw == 'Active' else False

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT 1
                FROM public.equipment_list
                WHERE UPPER(equipment_name) = %s
                  AND sub_category_id = %s
                  AND id <> %s
                LIMIT 1
            """, [equipment_name, subcategory_id, equipment_id])

            if cursor.fetchone():
                return JsonResponse({
                    'success': False,
                    'message': 'This equipment already exists in the selected subcategory.'
                }, status=400)

            cursor.execute("""
                UPDATE public.equipment_list
                SET equipment_name = %s,
                    sub_category_id = %s,
                    category_type = %s,
                    dimension_height = %s,
                    dimension_width = %s,
                    dimension_length = %s,
                    weight = %s,
                    volume = %s,
                    hsn_no = %s,
                    country_origin = %s,
                    status = %s
                WHERE id = %s
            """, [
                equipment_name,
                subcategory_id,
                category_name,
                dimension_h,
                dimension_w,
                dimension_l,
                weight,
                volume,
                hsn_no or None,
                country_origin or None,
                status_value,
                equipment_id
            ])

        image_1_path = save_uploaded_file(attachment_1) if attachment_1 else None
        image_2_path = save_uploaded_file(attachment_2) if attachment_2 else None
        image_3_path = save_uploaded_file(attachment_3) if attachment_3 else None

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT id
                FROM public.equipment_list_attachments
                WHERE equipment_list_id = %s
                LIMIT 1
            """, [equipment_id])
            attachment_row = cursor.fetchone()

            if attachment_row:
                update_fields = []
                params = []

                if image_1_path:
                    update_fields.append("image_1 = %s")
                    params.append(image_1_path)
                if image_2_path:
                    update_fields.append("image_2 = %s")
                    params.append(image_2_path)
                if image_3_path:
                    update_fields.append("image_3 = %s")
                    params.append(image_3_path)

                if update_fields:
                    params.append(equipment_id)
                    cursor.execute(f"""
                        UPDATE public.equipment_list_attachments
                        SET {", ".join(update_fields)}
                        WHERE equipment_list_id = %s
                    """, params)
            else:
                cursor.execute("""
                    INSERT INTO public.equipment_list_attachments (
                        equipment_list_id, image_1, image_2, image_3
                    )
                    VALUES (%s, %s, %s, %s)
                """, [equipment_id, image_1_path, image_2_path, image_3_path])

        return JsonResponse({
            'success': True,
            'message': 'Equipment updated successfully.'
        })

    except Exception as e:
        print("UPDATE_EQUIPMENT ERROR:", str(e))
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


def delete_equipment_id(request):
    print('Check the delete equipment id is working.')

    if request.method != 'POST':
        return JsonResponse({
            'status': 'error',
            'message': 'Invalid request method'
        }, status=405)

    equip_id = request.POST.get('equipId')
    print('Check the Equip ID:', equip_id)

    if not equip_id:
        return JsonResponse({
            'status': 'error',
            'message': 'Equipment ID is required'
        }, status=400)

    try:
        with connection.cursor() as cursor:
            print('Check the cursor connection is done.')
            cursor.execute(
                "SELECT public.delete_equipment_func(%s);",
                [equip_id]
            )
            result = cursor.fetchone()

        message = result[0] if result else 'No response from delete function'
        print('Delete function message:', message)

        if message == 'Equipment deleted successfully!':
            return JsonResponse({
                'status': 'success',
                'message': message
            })

        return JsonResponse({
            'status': 'error',
            'message': message
        }, status=400)

    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=500)


def subcategory_dropdown(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT id, category_name, name FROM get_sub()')
            subcategories = [{'id': row[0], 'category_name': row[1], 'name': row[2]} for row in cursor.fetchall()]
            print('sub category fetched successfully:', subcategories)
            return JsonResponse({'subcategories': subcategories}, safe=False)
    except Exception as e:
        # Handle exceptions, maybe log the error for debugging
        print("Error fetching sub category:", e)
        return JsonResponse({'subcategories': []})


def get_category_name(request):
    try:
        subcategory_id = request.GET.get('subcategory_id')
        # Fetch category name based on subcategory_id
        with connection.cursor() as cursor:
            cursor.execute('SELECT category_name FROM get_sub() WHERE id = %s', [subcategory_id])
            row = cursor.fetchone()
            category_name = row[0] if row else None
        return JsonResponse({'category_name': category_name})
    except Exception as e:
        # Handle exceptions, maybe log the error for debugging
        print("Error fetching category name:", e)
        return JsonResponse({'category_name': None})

def equipment_list(request):
    print('fetch the data')
    username = request.session.get('username')
    subcategory_id = request.GET.get('subcategory_id')
    if not subcategory_id:
        subcategory_id = None
    print('Sub category ID:', subcategory_id)

    try:
        subcategory_id = int(subcategory_id)
    except ValueError:
        return JsonResponse({'error': 'Invalid subcategory_id parameter'}, status=400)

    equipment_listing = []
    try:
        print('inside the list of try block')
        with connection.cursor() as cursor:
            print('inside the cursor object')
            cursor.execute("SELECT * FROM get_equipment_list(%s)", [subcategory_id])
            rows = cursor.fetchall()

            print('fetch the data')
            for row in rows:
                created_date = row[5].strftime('%d-%m-%Y') if row[5] else ''

                equipment_listing.append({
                    'id': row[0],
                    'equipment_name': row[1],
                    'sub_category_name': row[2],
                    'category_type': row[3],
                    'created_by': row[4],
                    'created_date': created_date
                })
    except Exception as e:
        print("Error fetching equipment list:", e)
        return JsonResponse({'error': str(e)}, status=500)

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'equipments': equipment_listing})

    context = {
        'equipment_listing': equipment_listing,
        'subcategory_id': subcategory_id,
        'username': username
    }
    return render(request, 'inventory/asset_creation.html', context)


def edit_subcategory_dropdown(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT id, category_name, name FROM get_sub()')
            sub = [{'id': row[0], 'category_name': row[1], 'name': row[2]} for row in cursor.fetchall()]
            print('sub category fetched successfully:', sub)
            return JsonResponse({'sub': sub}, safe=False)
    except Exception as e:
        # Handle exceptions, maybe log the error for debugging
        print("Error fetching sub category:", e)
        return JsonResponse({'sub': []})


def edit_get_category_name(request):
    try:
        subcategory_id = request.GET.get('subcategory_id')
        # Fetch category name based on subcategory_id
        with connection.cursor() as cursor:
            cursor.execute('SELECT category_name FROM get_sub() WHERE id = %s', [subcategory_id])
            row = cursor.fetchone()
            category_name = row[0] if row else None
        return JsonResponse({'category_name': category_name})
    except Exception as e:
        # Handle exceptions, maybe log the error for debugging
        print("Error fetching category name:", e)
        return JsonResponse({'category_name': None})


def fetch_stock_status(request, equipment_id):
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM get_stock_status(%s)", [equipment_id])
        stock_data = cursor.fetchone()
        print('Equipment ID:', equipment_id)
        print('Equipment ID:', equipment_id, stock_data)

    if stock_data is not None:
        unit_count = stock_data[0]
        stock_status = 'Stock in completed' if unit_count > 0 else 'Stock in pending'
    else:
        unit_count = 0
        stock_status = 'Stock in pending'
    return JsonResponse({'unit_count': unit_count, 'stock_status': stock_status})


def fetch_serial_barcode_no(request, equipment_id):
    # Execute the PostgreSQL function
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM get_serial_barcode_no(%s)", [equipment_id])
        rows = cursor.fetchall()
        if rows:
            # If multiple rows are returned, create a list of dictionaries
            data = [{'serial_number': row[0], 'barcode_number': row[1]} for row in rows]
        else:
            # If no rows are returned, return an error
            return JsonResponse({'error': 'No data found for equipment ID ' + str(equipment_id)}, status=404)

    return JsonResponse(data, safe=False)


def get_dimension_list(request, equipment_id):
    print('inside the function')
    # Execute the PostgreSQL function
    with connection.cursor() as cursor:
        print('inside the object of cursor')
        cursor.execute("SELECT * FROM get_dimension_list_stock(%s)", [equipment_id])
        rows = cursor.fetchall()  # Fetch all rows
        print('row values:', rows)
        if rows:
            # Initialize dictionaries to hold single and aggregated data
            dimension_details = {}
            stock_details = {
                'vender_name': '',
                'purchase_date': '',
                'unit_price': '',
                'rental_price': '',
                'reference_no': '',
                'unit': '',
                'serial_no': [],
                'barcode_no': []
            }

            # Extract common dimension details from the first row
            first_row = rows[0]
            dimension_details = {
                'dimension_height': first_row[0] or '',
                'dimension_width': first_row[1] or '',
                'dimension_length': first_row[2] or '',
                'weight': first_row[3] or '',
                'volume': first_row[4] or '',
                'hsn_no': first_row[5] or '',
                'country_origin': first_row[6] or '',
                'status': first_row[7] or '',
                'created_by': first_row[8] or '',
                'created_date': first_row[9].strftime('%d-%m-%Y') if first_row[9] else ''
            }

            # Check if any row has serial numbers or barcode numbers
            has_stock_details = any(row[16] or row[17] for row in rows)

            if has_stock_details:
                # Aggregate serial numbers and barcode numbers
                for row in rows:
                    stock_details['serial_no'].append(row[16] or '')
                    stock_details['barcode_no'].append(row[17] or '')

                # Assign single values to stock_details
                stock_details['vender_name'] = first_row[10] or ''
                stock_details['purchase_date'] = first_row[11].strftime('%d-%m-%Y') if first_row[11] else ''
                stock_details['unit_price'] = first_row[12] or ''
                stock_details['rental_price'] = first_row[13] or ''
                stock_details['reference_no'] = first_row[14] or ''
                stock_details['unit'] = first_row[15] or ''

            # Merge dictionaries
            data = {**dimension_details, **stock_details}
            print('Values are shown in the table are:', data)
        else:
            # If no rows are returned, return an error
            return JsonResponse({'error': 'No data found for equipment ID ' + str(equipment_id)}, status=404)

    return JsonResponse(data)


def stock_list(request):
    username = request.session.get('username')
    return render(request, 'product_tracking/Stock_details.html', {'username': username})


def fetch_stock_equipment_list(request):
    if request.method == 'POST':
        category_id = request.POST.get('category_type', '')
        start = int(request.POST.get('start', 0))
        limit = int(request.POST.get('limit', 10))

        print(f"Fetching data for category: {category_id}, start: {start}, limit: {limit}")

        # Fetch paginated data
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT * FROM public.get_list(%s) OFFSET %s LIMIT %s
            """, [category_id, start, limit])
            rows = cursor.fetchall()
            print(f"Fetched rows: {rows}")

        # Fetch total count
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*) FROM (
                    SELECT DISTINCT el.equipment_name, el.sub_category_id, el.category_type
                    FROM public.equipment_list el
                    LEFT JOIN public.sub_category sc ON el.sub_category_id = sc.id
                    LEFT JOIN public.stock_details sd ON el.id = sd.equipment_id
                    WHERE sc.category_id = %s
                ) AS distinct_items
            """, [category_id])
            total_items = cursor.fetchone()[0]
            print(f"Total items: {total_items}")

        equipment_list = []
        for row in rows:
            equipment_list.append({
                'equipment_name': row[0],
                'sub_category_name': row[1],  # Ensure this is the correct index for sub_category_name
                'category_type': row[2],
                'unit_price': row[3],
                'rental_price': row[4],
                'total_units': row[5],
            })

        print(f"Equipment list: {equipment_list}")

        return JsonResponse({'totalItems': total_items, 'data': equipment_list}, safe=False)
    else:
        return JsonResponse({'error': 'Invalid request'})


def stock_in(request, equipment_id):
    print('inside the stock in')
    try:
        print('Execute this try block')
        with connection.cursor() as cursor:
            print('Execute the cursor object')
            cursor.execute("SELECT * FROM public.fetch_stock_details(%s)", [equipment_id])
            rows = cursor.fetchall()
            print('Fetch the stock_details:', rows)

        if rows:
            print('inside the rows')
            data = [{'id': row[0], 'serial_number': row[1], 'barcode_number': row[2], 'vendor_name': row[3],
                     'unit_price': row[4],
                     'rental_price': row[5], 'purchase_date': row[6], 'reference_no': row[7]} for row in rows]
            print('insert the correct data:', data)
        else:
            return JsonResponse({'error': 'No data found for equipment ID ' + str(equipment_id)}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

    # Return the data as a JSON response
    return JsonResponse(data, safe=False)


def update_stock_in(request, row_id):
    if request.method == 'POST':
        try:
            vender_name = request.POST.get('vender_name')
            serial_number = request.POST.get('serial_number')
            barcode_number = request.POST.get('barcode_number')
            unit_price = request.POST.get('unit_price')
            rental_price = request.POST.get('rental_price')
            purchase_date = request.POST.get('purchase_date')
            reference_no = request.POST.get('reference_no')

            with connection.cursor() as cursor:
                cursor.callproc('update_stock_in_function', [
                    row_id,
                    vender_name,
                    serial_number,
                    barcode_number,
                    unit_price,
                    rental_price,
                    purchase_date,
                    reference_no
                ])
            return JsonResponse({'success': True, 'message': 'Updated successfully'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e), 'message': 'Not Updated successfully'})
    else:
        return JsonResponse({'success': False, 'error': 'Invalid request method.'})


def fetch_stock_details_by_name(request):
    equipment_name = request.GET.get('equipment_name', '')

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT *
            FROM public.fetch_stock_details_by_name(%s)
            """,
            [equipment_name]
        )
        results = cursor.fetchall()

    # Format the results into a JSON response
    response_data = []
    for row in results:
        data = {
            'serial_number': row[0],
            'barcode_number': row[1],
            'vendor_name': row[2],
            'unit_price': row[3],
            'rental_price': row[4],
            'purchase_date': row[5],
            'reference_no': row[6],
        }
        response_data.append(data)

    return JsonResponse(response_data, safe=False)


def company_warehouse_master_page(request):
    username = request.session.get('username')
    return render(request, 'inventory/company_warehouse_master.html', {'username': username})


def company_list(request):
    company_listing = []
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                           SELECT *
                           FROM public.manage_company_master('get', NULL, NULL, NULL, NULL, NULL, NULL)
                           """)
            rows = cursor.fetchall()

            for row in rows:
                company_listing.append({
                    'id': row[0],
                    'name': row[1],
                    'gst_no': row[2],
                    'email': row[3],
                    'company_logo': row[4],
                    'address': row[5],
                    'message': row[6],
                    'success': row[7],
                })

        return JsonResponse({'data': company_listing})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
def add_company(request):
    if request.method == 'POST':
        try:
            name = request.POST.get('name')
            gst_no = request.POST.get('gst_no')
            email = request.POST.get('email')
            address = request.POST.get('address')
            company_logo = None

            logo_file = request.FILES.get('company_logo')
            if logo_file:
                upload_result = cloudinary.uploader.upload(logo_file, folder="company_logo/")
                company_logo = upload_result.get('secure_url')

            with connection.cursor() as cursor:
                cursor.execute("""
                               SELECT *
                               FROM public.manage_company_master(%s, %s, %s, %s, %s, %s, %s)
                               """, [
                                   'add',
                                   None,
                                   name,
                                   gst_no,
                                   email,
                                   company_logo,
                                   address
                               ])
                row = cursor.fetchone()

            if row and row[7]:
                return JsonResponse({
                    'success': row[6],
                    'data': {
                        'id': row[0],
                        'name': row[1],
                        'gst_no': row[2],
                        'email': row[3],
                        'company_logo': row[4],
                        'address': row[5]
                    }
                })
            else:
                return JsonResponse({
                    'error': row[6] if row else 'Failed to add company'
                }, status=400)

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'error': 'Invalid request method'}, status=405)


@csrf_exempt
def update_company(request, id):
    if request.method == 'POST':
        try:
            name = request.POST.get('name')
            gst_no = request.POST.get('gst_no')
            email = request.POST.get('email')
            address = request.POST.get('address')
            company_logo = None

            logo_file = request.FILES.get('company_logo')
            if logo_file:
                upload_result = cloudinary.uploader.upload(logo_file, folder="company_logo/")
                company_logo = upload_result.get('secure_url')

            with connection.cursor() as cursor:
                cursor.execute("""
                               SELECT *
                               FROM public.manage_company_master(%s, %s, %s, %s, %s, %s, %s)
                               """, [
                                   'update',
                                   id,
                                   name,
                                   gst_no,
                                   email,
                                   company_logo,
                                   address
                               ])
                row = cursor.fetchone()

            if row and row[7]:
                return JsonResponse({
                    'success': row[6],
                    'data': {
                        'id': row[0],
                        'name': row[1],
                        'gst_no': row[2],
                        'email': row[3],
                        'company_logo': row[4],
                        'address': row[5]
                    }
                })
            else:
                return JsonResponse({
                    'error': row[6] if row else 'Failed to update company'
                }, status=400)

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'error': 'Invalid request method'}, status=405)


@csrf_exempt
def delete_company(request, id):
    if request.method == 'POST':
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                               SELECT *
                               FROM public.manage_company_master(%s, %s, %s, %s, %s, %s, %s)
                               """, [
                                   'delete',
                                   id,
                                   None,
                                   None,
                                   None,
                                   None,
                                   None
                               ])
                row = cursor.fetchone()

            if row and row[7]:
                return JsonResponse({'success': row[6]})
            else:
                return JsonResponse({
                    'error': row[6] if row else 'Failed to delete company'
                }, status=400)

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'error': 'Invalid request method'}, status=405)


def warehouse_list(request):
    warehouse_listing = []
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                           SELECT *
                           FROM public.manage_warehouse_master(
                                   %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                                )
                           """, [
                               'get',
                               None,
                               None,
                               None,
                               None,
                               None,
                               None,
                               None,
                               None,
                               None,
                               None,
                               None,
                               None,
                               None
                           ])
            rows = cursor.fetchall()

            for row in rows:
                warehouse_listing.append({
                    'id': row[0],
                    'company_id': row[1],
                    'company_name': row[2],
                    'warehouse_code': row[3],
                    'warehouse_name': row[4],
                    'contact_person': row[5],
                    'phone_no': row[6],
                    'email': row[7],
                    'warehouse_address': row[8],
                    'city': row[9],
                    'state': row[10],
                    'pincode': row[11],
                    'status': row[12],
                    'created_by': row[13],
                    'created_date': row[14].strftime('%Y-%m-%d %H:%M:%S') if row[14] else None,
                    'message': row[15],
                    'success': row[16],
                })

        return JsonResponse({'data': warehouse_listing})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
def add_warehouse(request):
    if request.method == 'POST':
        try:
            company_id = request.POST.get('company_id')
            warehouse_code = request.POST.get('warehouse_code')
            warehouse_name = request.POST.get('warehouse_name')
            contact_person = request.POST.get('contact_person')
            phone_no = request.POST.get('phone_no')
            email = request.POST.get('email')
            warehouse_address = request.POST.get('warehouse_address')
            city = request.POST.get('city')
            state = request.POST.get('state')
            pincode = request.POST.get('pincode')
            status = request.POST.get('status', 'true').lower() == 'true'
            created_by = request.session.get('user_id')

            with connection.cursor() as cursor:
                cursor.execute("""
                               SELECT *
                               FROM public.manage_warehouse_master(
                                       %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                                    )
                               """, [
                                   'add',
                                   None,
                                   int(company_id) if company_id else None,
                                   warehouse_code,
                                   warehouse_name,
                                   contact_person,
                                   phone_no,
                                   email,
                                   warehouse_address,
                                   city,
                                   state,
                                   pincode,
                                   status,
                                   created_by
                               ])
                row = cursor.fetchone()

            if row and row[16]:
                return JsonResponse({
                    'success': row[15],
                    'data': {
                        'id': row[0],
                        'company_id': row[1],
                        'company_name': row[2],
                        'warehouse_code': row[3],
                        'warehouse_name': row[4],
                        'contact_person': row[5],
                        'phone_no': row[6],
                        'email': row[7],
                        'warehouse_address': row[8],
                        'city': row[9],
                        'state': row[10],
                        'pincode': row[11],
                        'status': row[12],
                        'created_by': row[13],
                        'created_date': row[14].strftime('%Y-%m-%d %H:%M:%S') if row[14] else None,
                    }
                })
            else:
                return JsonResponse({
                    'error': row[15] if row else 'Failed to add warehouse'
                }, status=400)

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'error': 'Invalid request method'}, status=405)


@csrf_exempt
def add_warehouse(request):
    if request.method == 'POST':
        try:
            company_id = request.POST.get('company_id')
            warehouse_code = request.POST.get('warehouse_code')
            warehouse_name = request.POST.get('warehouse_name')
            contact_person = request.POST.get('contact_person')
            phone_no = request.POST.get('phone_no')
            email = request.POST.get('email')
            warehouse_address = request.POST.get('warehouse_address')
            city = request.POST.get('city')
            state = request.POST.get('state')
            pincode = request.POST.get('pincode')
            status = request.POST.get('status', 'true').lower() == 'true'
            created_by = request.session.get('user_id')

            with connection.cursor() as cursor:
                cursor.execute("""
                               SELECT *
                               FROM public.manage_warehouse_master(
                                       %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                                    )
                               """, [
                                   'add',
                                   None,
                                   int(company_id) if company_id else None,
                                   warehouse_code,
                                   warehouse_name,
                                   contact_person,
                                   phone_no,
                                   email,
                                   warehouse_address,
                                   city,
                                   state,
                                   pincode,
                                   status,
                                   created_by
                               ])
                row = cursor.fetchone()

            if row and row[16]:
                return JsonResponse({
                    'success': row[15],
                    'data': {
                        'id': row[0],
                        'company_id': row[1],
                        'company_name': row[2],
                        'warehouse_code': row[3],
                        'warehouse_name': row[4],
                        'contact_person': row[5],
                        'phone_no': row[6],
                        'email': row[7],
                        'warehouse_address': row[8],
                        'city': row[9],
                        'state': row[10],
                        'pincode': row[11],
                        'status': row[12],
                        'created_by': row[13],
                        'created_date': row[14].strftime('%Y-%m-%d %H:%M:%S') if row[14] else None,
                    }
                })
            else:
                return JsonResponse({
                    'error': row[15] if row else 'Failed to add warehouse'
                }, status=400)

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'error': 'Invalid request method'}, status=405)


@csrf_exempt
def update_warehouse(request, id):
    if request.method == 'POST':
        try:
            company_id = request.POST.get('company_id')
            warehouse_code = request.POST.get('warehouse_code')
            warehouse_name = request.POST.get('warehouse_name')
            contact_person = request.POST.get('contact_person')
            phone_no = request.POST.get('phone_no')
            email = request.POST.get('email')
            warehouse_address = request.POST.get('warehouse_address')
            city = request.POST.get('city')
            state = request.POST.get('state')
            pincode = request.POST.get('pincode')
            status = request.POST.get('status', 'true').lower() == 'true'
            created_by = request.session.get('user_id')

            with connection.cursor() as cursor:
                cursor.execute("""
                               SELECT *
                               FROM public.manage_warehouse_master(
                                       %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                                    )
                               """, [
                                   'update',
                                   id,
                                   int(company_id) if company_id else None,
                                   warehouse_code,
                                   warehouse_name,
                                   contact_person,
                                   phone_no,
                                   email,
                                   warehouse_address,
                                   city,
                                   state,
                                   pincode,
                                   status,
                                   created_by
                               ])
                row = cursor.fetchone()

            if row and row[16]:
                return JsonResponse({
                    'success': row[15],
                    'data': {
                        'id': row[0],
                        'company_id': row[1],
                        'company_name': row[2],
                        'warehouse_code': row[3],
                        'warehouse_name': row[4],
                        'contact_person': row[5],
                        'phone_no': row[6],
                        'email': row[7],
                        'warehouse_address': row[8],
                        'city': row[9],
                        'state': row[10],
                        'pincode': row[11],
                        'status': row[12],
                        'created_by': row[13],
                        'created_date': row[14].strftime('%Y-%m-%d %H:%M:%S') if row[14] else None,
                    }
                })
            else:
                return JsonResponse({
                    'error': row[15] if row else 'Failed to update warehouse'
                }, status=400)

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'error': 'Invalid request method'}, status=405)


@csrf_exempt
def delete_warehouse(request, id):
    if request.method == 'POST':
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                               SELECT *
                               FROM public.manage_warehouse_master(
                                       %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                                    )
                               """, [
                                   'delete',
                                   id,
                                   None,
                                   None,
                                   None,
                                   None,
                                   None,
                                   None,
                                   None,
                                   None,
                                   None,
                                   None,
                                   None,
                                   None
                               ])
                row = cursor.fetchone()

            if row and row[16]:
                return JsonResponse({'success': row[15]})
            else:
                return JsonResponse({
                    'error': row[15] if row else 'Failed to delete warehouse'
                }, status=400)

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'error': 'Invalid request method'}, status=405)


def get_warehouse_by_id(request, id):
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                           SELECT *
                           FROM public.manage_warehouse_master(
                                   %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                                )
                           """, [
                               'getbyid',
                               id,
                               None,
                               None,
                               None,
                               None,
                               None,
                               None,
                               None,
                               None,
                               None,
                               None,
                               None,
                               None
                           ])
            row = cursor.fetchone()

        if row and row[16]:
            return JsonResponse({
                'data': {
                    'id': row[0],
                    'company_id': row[1],
                    'company_name': row[2],
                    'warehouse_code': row[3],
                    'warehouse_name': row[4],
                    'contact_person': row[5],
                    'phone_no': row[6],
                    'email': row[7],
                    'warehouse_address': row[8],
                    'city': row[9],
                    'state': row[10],
                    'pincode': row[11],
                    'status': row[12],
                    'created_by': row[13],
                    'created_date': row[14].strftime('%Y-%m-%d %H:%M:%S') if row[14] else None,
                }
            })
        else:
            return JsonResponse({'error': row[15] if row else 'Warehouse not found'}, status=404)

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
