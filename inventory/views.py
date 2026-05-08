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
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)

def dashboard(request):
    with connection.cursor() as cursor:

        # 🔹 MAIN SUMMARY
        cursor.execute("SELECT * FROM get_dashboard_summary()")
        summary = cursor.fetchone()

        # 🔹 CATEGORY OVERVIEW
        cursor.execute("""
            SELECT mc.category_name, COUNT(sd.id)
            FROM master_category mc
            LEFT JOIN sub_category sc ON sc.category_id = mc.category_id
            LEFT JOIN equipment_list el ON el.sub_category_id = sc.id
            LEFT JOIN stock_details sd ON sd.equipment_id = el.id
            GROUP BY mc.category_name
        """)
        raw_categories = cursor.fetchall()

        categories = [
            {"name": row[0], "count": row[1]}
            for row in raw_categories
        ]

        total_assets = sum(c["count"] for c in categories)

        cursor.execute("""
        SELECT id, title, client_name, show_start_date, show_end_date, status
        FROM jobs
        ORDER BY created_date DESC
        LIMIT 5
        """)

        recent_jobs = cursor.fetchall()

        # 🔹 EVENTS (FIXED)

    # ✅ MUST BE OUTSIDE cursor block
    context = {
        "revenue": summary[0],
        "active_rentals": summary[1],
        "total_items": summary[2],
        "available": summary[3],
        "rented": summary[4],
        "maintenance": summary[5],
        "pickups": summary[6],
        "returns": summary[7],
        "dispatch": summary[8],
        "queue": summary[9],
    }

    return render(request, "inventory/dashboard.html", context)


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

            # -------------------------------------------------
            # DEBUG LOGS
            # -------------------------------------------------
            print("Received POST data:", request.POST)
            print("Received FILES data:", request.FILES)

            # -------------------------------------------------
            # EMPLOYEE TYPE
            # -------------------------------------------------
            employee_type = request.POST.get(
                'employee_type',
                ''
            ).strip()

            if not employee_type:

                return JsonResponse({
                    'error': 'Employee Type is required.'
                }, status=400)

            # -------------------------------------------------
            # EMPLOYEE ID
            # -------------------------------------------------
            employee_id_raw = request.POST.get(
                'employee_id',
                ''
            ).strip()

            employee_id = None

            # -------------------------------------------------
            # EMAIL
            # -------------------------------------------------
            email_raw = request.POST.get(
                'email',
                ''
            ).strip()

            email = email_raw if email_raw else None

            # -------------------------------------------------
            # PERMANENT STAFF VALIDATION
            # -------------------------------------------------
            if employee_type == 'Permanent':

                # Employee ID mandatory
                if not employee_id_raw:

                    return JsonResponse({
                        'error': 'Employee ID is required for permanent employees.'
                    }, status=400)

                # Email mandatory
                if not email:

                    return JsonResponse({
                        'error': 'Email is required for permanent employees.'
                    }, status=400)

                try:

                    employee_id = int(employee_id_raw)

                except ValueError:

                    return JsonResponse({
                        'error': 'Employee ID must be numeric.'
                    }, status=400)

            # -------------------------------------------------
            # BASIC DETAILS
            # -------------------------------------------------
            name = request.POST.get(
                'name',
                ''
            ).strip()

            designation = request.POST.get(
                'designation',
                ''
            ).strip()

            gender = request.POST.get(
                'gender',
                ''
            ).strip() or None

            # -------------------------------------------------
            # MOBILE NUMBER
            # -------------------------------------------------
            mobile_no_raw = request.POST.get(
                'mobile_no',
                ''
            ).strip()

            if not mobile_no_raw:

                return JsonResponse({
                    'error': 'Mobile number is required.'
                }, status=400)

            try:

                mobile_no = int(mobile_no_raw)

            except ValueError:

                return JsonResponse({
                    'error': 'Mobile number must be numeric.'
                }, status=400)

            # -------------------------------------------------
            # DATES
            # -------------------------------------------------
            joining_date_raw = request.POST.get(
                'joining_date',
                ''
            ).strip()

            dob_raw = request.POST.get(
                'dob',
                ''
            ).strip()

            joining_date = None
            dob = None

            if joining_date_raw:

                joining_date = datetime.strptime(
                    joining_date_raw,
                    '%Y-%m-%d'
                ).date()

            if dob_raw:

                dob = datetime.strptime(
                    dob_raw,
                    '%Y-%m-%d'
                ).date()

            # -------------------------------------------------
            # OTHER DETAILS
            # -------------------------------------------------
            reporting_id = request.POST.get(
                'reporting',
                ''
            ).strip()

            p_address = request.POST.get(
                'p_address',
                ''
            ).strip() or None

            c_address = request.POST.get(
                'c_address',
                ''
            ).strip() or None

            country = request.POST.get(
                'country',
                ''
            ).strip() or None

            state = request.POST.get(
                'state',
                ''
            ).strip() or None

            status = request.POST.get(
                'status',
                'true'
            ).lower() == 'true'

            blood_group = request.POST.get(
                'bloodGroup',
                ''
            ).strip() or None

            created_by = request.session.get('user_id')

            created_date = datetime.now().replace(
                tzinfo=None
            )

            # -------------------------------------------------
            # FILES
            # -------------------------------------------------
            profile_photo = request.FILES.get(
                'profile_photo'
            )

            attachment_images = request.FILES.getlist(
                'attachments[]'
            )

            # -------------------------------------------------
            # PROFILE PHOTO
            # -------------------------------------------------
            profile_photo_url = None

            if profile_photo:

                max_size = 5 * 1024 * 1024

                if profile_photo.size > max_size:

                    return JsonResponse({
                        'error': 'Profile photo size must not exceed 5MB.'
                    }, status=400)

                upload_result = cloudinary.uploader.upload(
                    profile_photo,
                    folder="profilepic/"
                )

                profile_photo_url = upload_result.get(
                    'secure_url'
                )

            # -------------------------------------------------
            # ATTACHMENTS
            # -------------------------------------------------
            image_urls = []

            for image in attachment_images[:2]:

                if image:

                    upload_result = cloudinary.uploader.upload(
                        image,
                        folder="uploads/"
                    )

                    image_urls.append(
                        upload_result.get('secure_url')
                    )

            while len(image_urls) < 2:
                image_urls.append(None)

            # -------------------------------------------------
            # DUPLICATE CHECK
            # -------------------------------------------------
            with connection.cursor() as cursor:

                cursor.execute("""
                    SELECT COUNT(*)
                    FROM employee
                    WHERE
                        (%s IS NOT NULL AND employee_id = %s)
                        OR
                        (%s IS NOT NULL AND email = %s)
                        OR
                        mobile_no = %s
                """, [

                    employee_id,
                    employee_id,

                    email,
                    email,

                    mobile_no

                ])

                duplicate_count = cursor.fetchone()[0]

            if duplicate_count > 0:

                return JsonResponse({
                    'error': 'Employee with this Employee ID, email, or mobile number already exists.'
                }, status=400)

            # -------------------------------------------------
            # REPORTING NAME
            # -------------------------------------------------
            reporting_name = None

            if reporting_id:

                with connection.cursor() as cursor:

                    cursor.execute("""
                        SELECT name
                        FROM employee
                        WHERE id = %s
                    """, [reporting_id])

                    reporting_row = cursor.fetchone()

                if reporting_row is None:

                    return JsonResponse({
                        'error': 'Invalid reporting ID.'
                    }, status=400)

                reporting_name = reporting_row[0]

            # -------------------------------------------------
            # POSTGRESQL FUNCTION
            # -------------------------------------------------
            try:

                with connection.cursor() as cursor:

                    cursor.callproc('add_employee', [

                        employee_id,
                        employee_type,

                        name,
                        email,
                        designation,
                        mobile_no,
                        gender,

                        joining_date,
                        dob,

                        reporting_name,

                        p_address,
                        c_address,

                        country,
                        state,

                        status,

                        blood_group,

                        created_by,
                        created_date,

                        profile_photo_url,

                        image_urls[0],
                        image_urls[1]

                    ])

            except IntegrityError as e:

                return JsonResponse({
                    'error': 'Integrity error occurred: ' + str(e)
                }, status=400)

            # -------------------------------------------------
            # SUCCESS
            # -------------------------------------------------
            return JsonResponse({
                'success': 'Employee added successfully'
            }, status=200)

        except Exception as e:

            print(f"An unexpected error occurred: {str(e)}")

            return JsonResponse({
                'error': 'An unexpected error occurred: ' + str(e)
            }, status=500)

    return render(
        request,
        'inventory/Employee_master.html',
        {
            'employees': get_all_employees(),
            'username': username
        }
    )

def fetch_vehicle_list(request):

    try:

        with connection.cursor() as cursor:

            cursor.execute("""
                SELECT
                    id,
                    vehicle_number,
                    vehicle_name
                FROM public.transport_master
                WHERE status = true
                ORDER BY vehicle_number
            """)

            rows = cursor.fetchall()

        vehicles = []

        for row in rows:

            vehicles.append({
                "id": row[0],
                "vehicle_no": row[1],
                "vehicle_name": row[2]
            })

        return JsonResponse({
            "vehicles": vehicles
        })

    except Exception as e:

        print("FETCH VEHICLE LIST ERROR:", str(e))

        return JsonResponse({
            "error": str(e)
        }, status=500)

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
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    e.id,
                    e.employee_id,
                    e.employee_type,
                    e.name,
                    e.email,
                    e.mobile_no,
                    e.designation,
                    e.gender,
                    e.joining_date,
                    e.dob,
                    e.reporting,
                    e.p_address,
                    e.c_address,
                    e.country,
                    e.state,
                    e.blood_group,
                    e.status,
                    COALESCE(
                        json_agg(ei.images) FILTER (WHERE ei.images IS NOT NULL),
                        '[]'
                    ) AS images
                FROM public.employee e
                LEFT JOIN public.employee_images ei
                    ON ei.employee_id = e.id
                GROUP BY e.id
                ORDER BY e.id DESC
            """)

            rows = cursor.fetchall()

        data = []

        for row in rows:
            data.append({
                "id": row[0],
                "employee_id": row[1],
                "employee_type": row[2],
                "name": row[3],
                "email": row[4],
                "mobile_no": row[5],
                "designation": row[6],
                "gender": row[7],
                "joining_date": row[8].strftime("%Y-%m-%d") if row[8] else "",
                "dob": row[9].strftime("%Y-%m-%d") if row[9] else "",
                "reporting": row[10],
                "p_address": row[11],
                "c_address": row[12],
                "country": row[13],
                "state": row[14],
                "blood_group": row[15],
                "status": row[16],
                "images": row[17] or []
            })

        return JsonResponse({
            "data": data
        })

    except Exception as e:
        return JsonResponse({
            "error": str(e)
        }, status=500)


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

    try:
        with connection.cursor() as cursor:

            cursor.execute("SELECT * FROM get_equipment_list(NULL)")
            rows = cursor.fetchall()

            for row in rows:
                created_date = row[5].strftime('%d-%m-%Y') if row[5] else ''
                equipment_listing.append({
                    'id': row[0],
                    'equipment_name': row[1],
                    'sub_category_name': row[2],
                    'category_type': row[3],
                    'created_by': row[4],
                    'created_date': created_date,
                })

            cursor.execute("SELECT id, category_name, name FROM get_sub()")
            subcategories = [
                {'id': row[0], 'category_name': row[1], 'name': row[2]}
                for row in cursor.fetchall()
            ]

    except Exception as e:
        print("ERROR:", e)

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
        created_date = datetime.datetime.now()

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


def add_equipment(request):
    if request.method == 'POST':
        equipment_name = request.POST.get('equipment_name', '').strip().upper()
        subcategory_id = request.POST.get('subcategory_id')
        category_name = request.POST.get('category_name', '').strip().upper()
        type_value = request.POST.get('type', '').strip() or None
        dimension_h = request.POST.get('dimension_h', '').strip() or None
        dimension_w = request.POST.get('dimension_w', '').strip() or None
        dimension_l = request.POST.get('dimension_l', '').strip() or None
        weight = request.POST.get('weight', '').strip() or None
        volume = request.POST.get('volume', '').strip() or None
        hsn_no = request.POST.get('hsn_no', '').strip() or None
        country_origin = request.POST.get('country_origin', '').strip() or None
        status = request.POST.get('status')
        created_by = request.session.get('user_id')
        created_date = datetime.now()

        attachment = request.FILES.get('attachment')
        attachment_path = None

        if attachment:
            attachment_path = os.path.join(settings.MEDIA_ROOT, 'attachments', attachment.name)
            os.makedirs(os.path.dirname(attachment_path), exist_ok=True)
            with open(attachment_path, 'wb') as f:
                for chunk in attachment.chunks():
                    f.write(chunk)

        try:
            subcategory_id = int(subcategory_id) if subcategory_id else None
            hsn_no = int(hsn_no) if hsn_no else None
        except (ValueError, TypeError):
            return JsonResponse({'success': False, 'message': 'Invalid numeric value provided.'})

        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT public.add_equipment_list(
                        %s::varchar,
                        %s::integer,
                        %s::varchar,
                        %s::varchar,
                        %s::varchar,
                        %s::varchar,
                        %s::varchar,
                        %s::varchar,
                        %s::varchar,
                        %s::integer,
                        %s::varchar,
                        %s::varchar,
                        %s::varchar,
                        %s::integer,
                        %s::timestamp
                    );
                """, [
                    equipment_name,
                    subcategory_id,
                    category_name,
                    type_value,
                    dimension_h,
                    dimension_w,
                    dimension_l,
                    weight,
                    volume,
                    hsn_no,
                    country_origin,
                    attachment_path,
                    status,
                    created_by,
                    created_date
                ])

            return JsonResponse({'success': True})

        except IntegrityError as e:
            error_message = str(e)
            if 'duplicate key value violates unique constraint "unique_equipment_name"' in error_message:
                error_message = 'Equipment name already exists. Please choose a different name.'
            return JsonResponse({'success': False, 'message': error_message})

        except Exception as e:
            print("An unexpected error occurred:", e)
            return JsonResponse({'success': False, 'message': str(e)})


def insert_vendor(request):
    if request.method == 'POST':
        # Retrieve form data
        vendor_name = request.POST.get('vendor_name')
        purchase_date = request.POST.get('purchase_date')
        unit_price = request.POST.get('unit_price')
        rental_price = request.POST.get('rental_price')
        reference_no = request.POST.get('reference_no')
        unit = request.POST.get('unitValue')
        attachment = request.FILES.get('attachment')

        # Extract dynamically generated input box values
        serial_numbers = []
        barcode_numbers = []
        for i in range(1, int(unit) + 1):
            serial_number = request.POST.get(f'serialNumber{i}', '')
            barcode_number = request.POST.get(f'barcodeNumber{i}', '')
            serial_numbers.append(serial_number)
            barcode_numbers.append(barcode_number)

        equipment_id = request.POST.get('equipmentId')
        subcategory_id = None
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT sub_category_id FROM equipment_list WHERE id = %s",
                    [equipment_id]
                )
                subcategory_id = cursor.fetchone()[0]
        except Exception as e:
            print(f"An unexpected error occurred while fetching equipment ID: {e}")

        # Handle file upload
        attachment_path = None
        if attachment:
            attachment_path = os.path.join(settings.MEDIA_ROOT, 'attachments', attachment.name)
            os.makedirs(os.path.dirname(attachment_path), exist_ok=True)  # Ensure the directory exists
            with open(attachment_path, 'wb') as f:
                for chunk in attachment.chunks():
                    f.write(chunk)

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT add_stock(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL);",
                    [equipment_id, vendor_name, purchase_date, unit_price, rental_price, reference_no, attachment_path,
                     unit, serial_numbers, barcode_numbers]
                )
            print('Stock Details added successfully')
            return redirect(f'/equipment_list/?subcategory_id={subcategory_id}')
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            return render(request, 'product_tracking/index.html', {'error': 'An unexpected error occurred'})
    else:
        # Handle GET request
        username = None
        if request.user.is_authenticated:
            username = request.user.username
        return render(request, 'product_tracking/performance.html', {'username': username})


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
    return render(request, 'inventory/Stock_details.html', {'username': username})

def get_categories(request):
    with connection.cursor() as cursor:
        # Fetch categories ordered by category_name in ascending order
        cursor.execute(
            "SELECT category_id, category_name FROM master_category WHERE status = TRUE ORDER BY category_name ASC")
        categories = cursor.fetchall()

    # Convert the result into a list of dictionaries
    category_list = [{'id': category[0], 'name': category[1]} for category in categories]

    # Determine the default category (first in alphabetical order)
    default_category = category_list[0] if category_list else None

    return JsonResponse({'categories': category_list, 'default_category': default_category})

def fetch_equipment_usages(request):
    equipment_id = request.GET.get('equipment_id')
    response_data = {'data': []}

    if equipment_id:
        with connection.cursor() as cursor:
            # Step 1: Get equipment name from equipment_list
            cursor.execute("SELECT equipment_name FROM equipment_list WHERE id = %s", [equipment_id])
            equipment_row = cursor.fetchone()

            if equipment_row:
                equipment_name = equipment_row[0]

                # Step 2: Get temp_id(s) from temp_equipment_details where equipment_name matches
                cursor.execute("""
                    SELECT temp_id
                    FROM temp_equipment_details
                    WHERE equipment_name = %s
                """, [equipment_name])

                temp_ids = cursor.fetchall()

                # Step 3: For each temp_id, get job_reference_no from temp table
                for temp_id_row in temp_ids:
                    temp_id = temp_id_row[0]

                    cursor.execute("""
                        SELECT job_reference_no
                        FROM temp
                        WHERE id = %s AND status = 'Delivery Challan'
                    """, [temp_id])

                    job_row = cursor.fetchone()
                    if job_row:
                        response_data['data'].append({
                            'temp_id': temp_id,
                            'job_reference_no': job_row[0]
                        })
            else:
                response_data['error'] = 'Equipment not found.'
    else:
        response_data['error'] = 'No Equipment ID provided.'

    return JsonResponse(response_data)

def fetch_stock_equipment_list(request):
    if request.method == 'POST':
        category_id = request.POST.get('category_type', '')
        start = int(request.POST.get('start', 0))
        limit = int(request.POST.get('limit', 10))

        print(f"Fetching data for category: {category_id}, start: {start}, limit: {limit}")

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    el.id,
                    el.equipment_name,
                    sc.name AS sub_category_name,
                    el.category_type,
                    COALESCE(MAX(sd.unit_price), 0) AS unit_price,
                    COALESCE(MAX(sd.rental_price), 0) AS rental_price,
                    COUNT(sd.id) AS total_units
                FROM public.equipment_list el
                LEFT JOIN public.sub_category sc
                    ON el.sub_category_id = sc.id
                LEFT JOIN public.stock_details sd
                    ON el.id = sd.equipment_id
                WHERE sc.category_id = %s
                GROUP BY el.id, el.equipment_name, sc.name, el.category_type
                ORDER BY el.equipment_name
                OFFSET %s LIMIT %s
            """, [category_id, start, limit])

            rows = cursor.fetchall()

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*)
                FROM public.equipment_list el
                LEFT JOIN public.sub_category sc
                    ON el.sub_category_id = sc.id
                WHERE sc.category_id = %s
            """, [category_id])

            total_items = cursor.fetchone()[0]

        equipment_list = [
            {
                'id': row[0],
                'equipment_name': row[1],
                'sub_category_name': row[2],
                'category_type': row[3],
                'unit_price': row[4],
                'rental_price': row[5],
                'total_units': row[6],
            }
            for row in rows
        ]

        return JsonResponse({'totalItems': total_items, 'data': equipment_list}, safe=False)

    return JsonResponse({'error': 'Invalid request'}, status=405)

def get_serial_details(request, equipment_id):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT
                serial_no,
                barcode_no
            FROM public.stock_details
            WHERE equipment_id = %s
            ORDER BY id
        """, [equipment_id])

        rows = cursor.fetchall()

    serial_details = [
        {
            "serial_no": row[0],
            "barcode_no": row[1],
        }
        for row in rows
    ]

    return JsonResponse({"serial_details": serial_details})

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

def transport_master_page(request):
    return render(request, 'inventory/transport_master.html')


def add_transport(request):
    if request.method == "POST":
        vehicle_name = request.POST.get("vehicle_name")
        vehicle_number = request.POST.get("vehicle_number")
        load_capacity = request.POST.get("load_capacity")
        created_by = request.session.get("user_id")
        transport_id = request.POST.get("id")

        documents = request.FILES.getlist('documents')

        try:
            with connection.cursor() as cursor:

                if transport_id:
                    cursor.execute(
                        "SELECT manage_transport(%s, %s, %s, %s, %s, %s)",
                        ['update', transport_id, vehicle_name, vehicle_number, load_capacity, created_by]
                    )
                    message = "Transport updated successfully"
                    transport_id = int(transport_id)

                else:
                    cursor.execute(
                        "SELECT manage_transport(%s, %s, %s, %s, %s, %s)",
                        ['create', None, vehicle_name, vehicle_number, load_capacity, created_by]
                    )
                    transport_id = cursor.fetchone()[0]
                    message = "Transport added successfully"

            # Save documents
            for file in documents:
                path = default_storage.save(f"transport_docs/{file.name}", file)

                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO transport_master_attachment (transport_master_id, attachment) VALUES (%s, %s)",
                        [transport_id, path]
                    )

            return JsonResponse({"success": True, "message": message})

        except Exception as e:
            return JsonResponse({"success": False, "message": str(e)})


def transport_list(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM get_transport_list()")
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()

    data = [dict(zip(columns, row)) for row in rows]

    return JsonResponse({"data": data})


from django.views.decorators.csrf import csrf_exempt

@csrf_exempt
def delete_transport(request, id):
    if request.method == "POST":
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT delete_transport(%s)", [id])

            return JsonResponse({"success": True, "message": "Deleted successfully"})

        except Exception as e:
            return JsonResponse({"success": False, "message": str(e)})

    return JsonResponse({"success": False, "message": "Invalid request"})

import json

def delete_transport_document(request):
    data = json.loads(request.body)
    transport_id = data.get("transport_id")
    file_path = data.get("file_path")

    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM transport_master_attachment WHERE transport_master_id=%s AND attachment=%s",
            [transport_id, file_path]
        )

    return JsonResponse({"success": True, "message": "Document deleted"})


def crew_master_page(request):
    return render(request, 'inventory/crew_master.html')


def add_crew(request):
    designation = request.POST.get("designation")
    crew_id = request.POST.get("id")
    created_by = request.session.get("user_id")

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT manage_crew(%s, %s, %s, %s)",
            ['update' if crew_id else 'create', crew_id, designation, created_by]
        )

    return JsonResponse({"success": True, "message": "Saved successfully"})


def crew_list(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM get_crew_list()")
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()

    return JsonResponse({"data": [dict(zip(columns, row)) for row in rows]})


def delete_crew(request, id):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT delete_crew(%s)", [id])

        return JsonResponse({"success": True, "message": "Deleted successfully"})

    except Exception as e:
        return JsonResponse({"success": False, "message": str(e)})


def event_creation(request):
    return render(request, 'inventory/event_creation.html')

def add_event(request):
    try:
        print("RAW POST:", request.POST)
        print("USER ID:", request.session.get("user_id"))

        event_id = request.POST.get("event_id")
        event_name = request.POST.get("event_name")
        from_date = request.POST.get("from_date")
        to_date = request.POST.get("to_date")
        location = request.POST.get("location")
        user_id = request.session.get("user_id")

        if event_id:
            event_id = int(event_id)
        else:
            event_id = None

        if not user_id:
            return JsonResponse({
                "success": False,
                "error": "User session expired. Please login again."
            })

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT manage_event(%s, %s, %s, %s, %s, %s)",
                [
                    event_id,
                    user_id,
                    event_name,
                    from_date,
                    to_date,
                    location
                ]
            )

        return JsonResponse({"success": True})

    except Exception as e:
        print("ERROR:", str(e))
        return JsonResponse({"success": False, "error": str(e)})

def event_list(request):
    start = request.GET.get("start")
    end = request.GET.get("end")

    print("START:", start, "END:", end)  # debug

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT * FROM get_event_list(%s, %s)",
            [start, end]
        )
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()

    return JsonResponse({"data": [dict(zip(columns, row)) for row in rows]})


def delete_event(request, id):
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM event_master WHERE event_id=%s",
            [id]
        )

    return JsonResponse({"success": True})

def crew_allocation_page(request):
    return render(request, "inventory/crew_allocation.html")

def get_events(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM get_event_dropdown()")
        cols = [c[0] for c in cursor.description]
        rows = cursor.fetchall()

    return JsonResponse({
        "data": [dict(zip(cols, row)) for row in rows]
    })

def get_employees(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM get_employee_dropdown()")
        cols = [c[0] for c in cursor.description]
        rows = cursor.fetchall()

    return JsonResponse({
        "data": [dict(zip(cols, row)) for row in rows]
    })

def get_areas(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM get_area_dropdown()")
        cols = [c[0] for c in cursor.description]
        rows = cursor.fetchall()

    return JsonResponse({
        "data": [dict(zip(cols, row)) for row in rows]
    })

def save_crew_allocation(request):
    event_id = request.POST.get("event_id")
    emp_ids = request.POST.getlist("emp_ids[]")
    area_id = request.POST.get("area_id")
    work_date = request.POST.get("work_date")

    with connection.cursor() as cursor:
        for emp_id in emp_ids:
            cursor.execute(
                "SELECT save_crew_allocation(%s, %s, %s, %s, %s)",
                [
                    event_id,
                    emp_id,
                    area_id,
                    work_date,
                    request.session.get("user_id")
                ]
            )

    return JsonResponse({"success": True})

def get_allocations(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM get_crew_allocation_list()")
        cols = [c[0] for c in cursor.description]
        rows = cursor.fetchall()

    return JsonResponse({
        "data": [dict(zip(cols, row)) for row in rows]
    })

def delete_crew_allocation(request, id):
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM crew_assign WHERE crew_id = %s",
                [id]
            )

        return JsonResponse({"success": True})

    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)})

def connects(request):
    return render(request, "inventory/connects.html")


def save_connect(request):

    data = request.POST
    operation = data.get("operation") or "CREATE"
    record_id = data.get("id") or None
    t = data.get("type")

    # 🔥 NORMALIZE DATA BASED ON TYPE
    name = email = mobile = None

    if t == "Company":
        name = data.get("company_name")
        email = data.get("company_email")

    elif t == "Venue":
        name = data.get("venue_name")

    elif t == "Individual":
        name = data.get("individual_name")
        email = data.get("individual_email")
        mobile = data.get("mobile_no")

    with connection.cursor() as cursor:
        cursor.callproc('connect_master', [
            operation,
            record_id,
            t,
            name,
            email,
            mobile,
            data.get('address') or data.get('office_address'),
            data.get('city'),
            data.get('country'),
            data.get('state'),
            data.get('post_code'),
            request.session.get("user_id", 1),
            datetime.now(),
            'Active',

            data.get('company_name'),
            data.get('gst_no'),
            data.get('pan_no'),

            None, None, None,  # contact person fields (unused)

            data.get('billing_address'),
            data.get('office_address'),
            data.get('social_no'),

            data.get('company'),
            data.get('venue_name'),
            data.get('venue_address'),

            data.get('individual_name'),
            data.get('individual_address'),
            data.get('mobile_no')
        ])

    return JsonResponse({"success": True})

def get_connects(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM connects")
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()

    data = [dict(zip(columns, row)) for row in rows]

    return JsonResponse({"data": data})

def delete_connect(request, id):
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM connects WHERE id = %s",
                [id]
            )

        return JsonResponse({"success": True})

    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)})

def equipment_stock_details(request, equipment_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    sd.id,
                    sd.serial_no,
                    sd.barcode_no,
                    sd.warehouse_id,
                    wm.warehouse_name
                FROM public.stock_details sd
                LEFT JOIN public.warehouse_master wm
                    ON wm.id = sd.warehouse_id
                WHERE sd.equipment_id = %s
                ORDER BY sd.id
            """, [equipment_id])

            rows = cursor.fetchall()

        stock_rows = []
        for row in rows:
            stock_rows.append({
                'id': row[0],
                'serial_no': row[1],
                'barcode_no': row[2],
                'warehouse_id': row[3],
                'warehouse_name': row[4] or '-',
            })

        return JsonResponse({
            'success': True,
            'stock_details': stock_rows
        })

    except Exception as e:
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
                    sc.name AS subcategory_name,
                    mc.category_name,
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
                    ela.image_3,
                    COUNT(sd.id) AS stock_qty,
                    COUNT(sd.id) FILTER (
                        WHERE COALESCE(sd.scan_flag, false) = false
                    ) AS available_qty,
                    MAX(sd.unit_price) AS unit_price,
                    MAX(sd.rental_price) AS rental_price
                FROM public.equipment_list el
                LEFT JOIN public.sub_category sc
                    ON sc.id = el.sub_category_id
                LEFT JOIN public.master_category mc
                    ON mc.category_id = sc.category_id
                LEFT JOIN public.equipment_list_attachments ela
                    ON ela.equipment_list_id = el.id
                LEFT JOIN public.stock_details sd
                    ON sd.equipment_id = el.id
                WHERE el.id = %s
                GROUP BY
                    el.id,
                    el.equipment_name,
                    sc.name,
                    mc.category_name,
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
            """, [equipment_id])

            row = cursor.fetchone()

        if not row:
            return JsonResponse({
                "success": False,
                "message": "Equipment not found."
            }, status=404)

        return JsonResponse({
            "success": True,
            "equipment": {
                "id": row[0],
                "equipment_name": row[1] or "",
                "subcategory_name": row[2] or "",
                "category_name": row[3] or "",
                "dimension_h": row[4] or "",
                "dimension_w": row[5] or "",
                "dimension_l": row[6] or "",
                "weight": row[7] or "",
                "volume": row[8] or "",
                "hsn_no": row[9] or "",
                "country_origin": row[10] or "",
                "status": "Active" if row[11] else "Inactive",
                "image_1": row[12] or "",
                "image_2": row[13] or "",
                "image_3": row[14] or "",
                "stock_qty": row[15] or 0,
                "available_qty": row[16] or 0,
                "unit_price": str(row[17]) if row[17] is not None else "",
                "rental_price": str(row[18]) if row[18] is not None else "",
                "warehouse": "-"
            }
        })

    except Exception as e:
        return JsonResponse({
            "success": False,
            "message": str(e)
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

def get_warehouses(request):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT id, warehouse_name
            FROM warehouse_master
            WHERE status = true
            ORDER BY warehouse_name
        """)

        rows = cursor.fetchall()

    return JsonResponse({
        "warehouses": [
            {"id": r[0], "warehouse_name": r[1]}
            for r in rows
        ]
    })
# JOB BOOK PAGE (GET + POST)

def job_book_list(request):
    username = request.session.get('username')
    return render(request, 'inventory/job_book.html', {
        'username': username
    })
def add_job(request):
    username = request.session.get('username')

    if request.method == 'POST':
        title = request.POST.get('title')
        client_name = request.POST.get('client_name')
        contact_person_name = request.POST.get('contact_person_name')
        contact_person_number = request.POST.get('contact_person_number')
        venue_name = request.POST.get('venue_name')
        venue_address = request.POST.get('venue_address')
        status = request.POST.get('status')

        crew_type = request.POST.get('crew_type')
        setup_date = request.POST.get('setup_date') or None
        rehearsal_date = request.POST.get('rehearsal_date') or None
        start_date = request.POST.get('start_date') or None
        end_date = request.POST.get('end_date') or None
        total_days = request.POST.get('total_days')

        amount_row = request.POST.get('amount_row')
        discount = request.POST.get('discount')
        discounted_amount = request.POST.get('discounted_amount')
        total_amount = request.POST.get('total_amount')
        input_notes = request.POST.get('input_notes')

        equipment_location = request.POST.get('equipment_location')
        equipment_incharge = request.POST.get('equipment_incharge')
        equipment_rehearsal_date = request.POST.get('equipment_rehearsal_date') or None
        equipment_event_date = request.POST.get('equipment_event_date') or None

        equipment_categories = request.POST.getlist('equipment_category[]')
        equipment_ids = request.POST.getlist('equipment_name[]')
        equipment_qtys = request.POST.getlist('equipment_qty[]')
        rental_prices = request.POST.getlist('rental_price[]')
        equipment_totals = request.POST.getlist('equipment_total[]')
        equipment_notes = request.POST.getlist('equipment_notes[]')

        created_by = request.session.get('user_id')

        if not created_by:
            return JsonResponse({
                'success': False,
                'error': 'User session expired. Please login again.'
            }, status=401)

        try:
            with transaction.atomic():
                with connection.cursor() as cursor:

                    # =====================================================
                    # CASE 1: QUOTATION SAVE IN TEMP TABLES
                    # =====================================================
                    if status == 'Quotation':

                        cursor.execute("""
                            SELECT public.manage_temp_job(
                                %s, %s,
                                %s, %s, %s, %s, %s,
                                %s, %s, %s,
                                %s, %s, %s, %s,
                                %s, %s, %s, %s, %s,
                                %s, %s
                            )
                        """, [
                            'CREATE_QUOTATION',
                            None,

                            title,
                            client_name,
                            contact_person_name,
                            contact_person_number,
                            status,

                            venue_name,
                            venue_address,
                            crew_type,

                            setup_date,
                            rehearsal_date,
                            start_date,
                            end_date,

                            total_days,
                            amount_row,
                            discount,
                            discounted_amount,
                            total_amount,

                            created_by,
                            input_notes
                        ])

                        temp_id = cursor.fetchone()[0]

                        for index, equipment_id in enumerate(equipment_ids):
                            if not equipment_id:
                                continue

                            cursor.execute("""
                                SELECT equipment_name
                                FROM public.equipment_list
                                WHERE id = %s
                            """, [equipment_id])

                            equipment_row = cursor.fetchone()
                            equipment_name = equipment_row[0] if equipment_row else ''

                            cursor.execute("""
                                INSERT INTO public.temp_equipment_details (
                                    temp_id,
                                    equipment_detail_id,
                                    location,
                                    incharge,
                                    equipment_setup_date,
                                    equipment_rehearsal_date,
                                    equipment_name,
                                    quantity,
                                    equipment_unit_price,
                                    equipment_total,
                                    equipment_notes
                                )
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """, [
                                temp_id,
                                equipment_id,
                                equipment_location,
                                equipment_incharge,
                                equipment_event_date,
                                equipment_rehearsal_date,
                                equipment_name,
                                equipment_qtys[index] if index < len(equipment_qtys) else '',
                                rental_prices[index] if index < len(rental_prices) else '',
                                equipment_totals[index] if index < len(equipment_totals) else '',
                                equipment_notes[index] if index < len(equipment_notes) else ''
                            ])

                        return JsonResponse({
                            'success': True,
                            'message': 'Quotation saved successfully.',
                            'temp_id': temp_id
                        })

                    # =====================================================
                    # CASE 2: DIRECT PROFORMA / PREPSHEET / DELIVERY
                    # SAVE IN JOBS TABLES
                    # =====================================================
                    else:

                        cursor.execute("""
                            SELECT public.manage_job(
                                %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s,
                                %s, %s
                            )
                        """, [
                            'CREATE_DIRECT_JOB',
                            None,
                            None,
                            None,
                            None,

                            title,
                            client_name,
                            contact_person_name,
                            contact_person_number,
                            status,

                            venue_address,
                            crew_type,
                            setup_date,
                            rehearsal_date,
                            start_date,

                            end_date,
                            total_days,
                            amount_row,
                            discount,
                            discounted_amount,

                            total_amount,
                            created_by
                        ])

                        job_id = cursor.fetchone()[0]

                        for index, equipment_id in enumerate(equipment_ids):
                            if not equipment_id:
                                continue

                            cursor.execute("""
                                SELECT equipment_name
                                FROM public.equipment_list
                                WHERE id = %s
                            """, [equipment_id])

                            equipment_row = cursor.fetchone()
                            equipment_name = equipment_row[0] if equipment_row else ''

                            cursor.execute("""
                                INSERT INTO public.job_details (
                                    job_id,
                                    category_name,
                                    equipment_name,
                                    quantity,
                                    number_of_days,
                                    amount
                                )
                                VALUES (%s, %s, %s, %s, %s, %s)
                            """, [
                                job_id,
                                equipment_categories[index] if index < len(equipment_categories) else '',
                                equipment_name,
                                equipment_qtys[index] if index < len(equipment_qtys) else '',
                                total_days,
                                equipment_totals[index] if index < len(equipment_totals) else ''
                            ])

                        return JsonResponse({
                            'success': True,
                            'message': f'{status} job saved successfully.',
                            'job_id': job_id
                        })

        except Exception as e:
            print("ADD_JOB ERROR:", str(e))
            return JsonResponse({
                'success': False,
                'error': str(e)
            }, status=500)

    return render(request, 'inventory/add_job.html', {
        'username': username
    })


def convert_to_proforma(request, temp_id):
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT public.manage_job(%s, %s, %s, %s, %s)",
                    [
                        'CONVERT_TO_PROFORMA',
                        temp_id,
                        None,
                        None,
                        None
                    ]
                )

                job_id = cursor.fetchone()[0]

        return redirect('inventory:job_book')

    except Exception as e:
        print("CONVERT_TO_PROFORMA ERROR:", str(e))
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


def create_split_job(request, job_id):
    if request.method != 'POST':
        return redirect('inventory:job_book')

    section = request.POST.get('job_section')

    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT public.manage_job(%s, %s, %s, %s, %s)",
                    [
                        'CREATE_SPLIT',
                        None,
                        None,
                        job_id,
                        section
                    ]
                )

                split_job_id = cursor.fetchone()[0]

        return redirect('inventory:job_book')

    except Exception as e:
        print("CREATE_SPLIT_JOB ERROR:", str(e))
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

def fetch_client_name(request):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT DISTINCT type, name, company_name
            FROM public.connects
        """)

        client_names = []

        for row in cursor.fetchall():
            client_type, name, company_name = row

            if name:
                client_names.append({
                    'type': client_type,
                    'name': name
                })

            if company_name:
                client_names.append({
                    'type': client_type,
                    'name': company_name
                })

    return JsonResponse({
        'client_names': client_names
    })


def fetch_venue_name(request):
    query = request.GET.get('query', '').strip()

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT DISTINCT venue_name
            FROM public.connects
            WHERE venue_name IS NOT NULL
              AND TRIM(venue_name) <> ''
              AND venue_name ILIKE %s
            ORDER BY venue_name
            LIMIT 20
        """, [f'%{query}%'])

        rows = cursor.fetchall()

    return JsonResponse({
        "venue_names": [{"name": row[0]} for row in rows]
    })



def fetch_venue_address(request):
    venue_name = request.GET.get('venue_name', '').strip()

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT venue_address
            FROM public.connects
            WHERE venue_name = %s
            LIMIT 1
        """, [venue_name])

        row = cursor.fetchone()

    return JsonResponse({
        "venue_address": row[0] if row else ""
    })


def fetch_individual_names(request):
    client_name = request.GET.get('client_name', '').strip()

    if not client_name:
        return JsonResponse({
            'individual_names': []
        })

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT name, mobile_no
            FROM public.connects
            WHERE company_name = %s
               OR name = %s
            ORDER BY name
        """, [client_name, client_name])

        rows = cursor.fetchall()

    return JsonResponse({
        'individual_names': [
            {
                'name': row[0],
                'mobile': row[1]
            }
            for row in rows
        ]
    })


def fetch_master_categories(request):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT category_id, category_name
            FROM public.master_category
            ORDER BY category_name
        """)

        rows = cursor.fetchall()

    return JsonResponse({
        'master_categories': [
            {
                'category_id': row[0],
                'category_name': row[1]
            }
            for row in rows
        ]
    })



def fetch_equipment_names(request):

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT id, equipment_name
            FROM public.equipment_list
            WHERE status = TRUE
            ORDER BY equipment_name
        """)

        rows = cursor.fetchall()

    print("TOTAL ROWS:", len(rows))

    for row in rows[:10]:
        print(row)

    return JsonResponse({
        "equipment_names": [
            {
                "id": row[0],
                "name": row[1]
            }
            for row in rows
        ]
    })


def fetch_rental_price(request):
    equipment_id = request.GET.get("equipment_id")

    if not equipment_id:
        return JsonResponse({
            "error": "Equipment ID is required"
        }, status=400)

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT
                mc.category_name,
                sc.name AS sub_category_name,
                COALESCE(MAX(sd.rental_price), 0) AS rental_price
            FROM public.equipment_list el
            JOIN public.sub_category sc
                ON el.sub_category_id = sc.id
            JOIN public.master_category mc
                ON sc.category_id = mc.category_id
            LEFT JOIN public.stock_details sd
                ON sd.equipment_id = el.id
            WHERE el.id = %s
            GROUP BY mc.category_name, sc.name
            LIMIT 1
        """, [equipment_id])

        row = cursor.fetchone()

    if not row:
        return JsonResponse({
            "error": "Equipment not found"
        }, status=404)

    return JsonResponse({
        "category_name": row[0],
        "sub_category_name": row[1],
        "rental_price": float(row[2])
    })


def get_employee_name(request):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT DISTINCT id, name
            FROM public.employee
            ORDER BY name
        """)

        rows = cursor.fetchall()

    return JsonResponse({
        'employee_names': [
            {
                'id': row[0],
                'name': row[1]
            }
            for row in rows
        ]
    })


def jobs_list(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    id,
                    'QUOTATION' AS record_type,
                    job_reference_no,
                    quotation_no,
                    main_job_no,
                    job_order_no,
                    title,
                    client_name,
                    venue_name,
                    status,
                    created_date
                FROM public.temp

                UNION ALL

                SELECT
                    id,
                    'JOB' AS record_type,
                    job_reference_no,
                    quotation_no,
                    main_job_no,
                    job_order_no,
                    title,
                    client_name,
                    NULL AS venue_name,
                    status,
                    created_date
                FROM public.jobs

                ORDER BY created_date DESC NULLS LAST, id DESC
            """)

            rows = cursor.fetchall()

        data = []

        for row in rows:
            data.append({
                "id": row[0],
                "record_type": row[1],
                "job_reference_no": row[2],
                "quotation_no": row[3],
                "main_job_no": row[4],
                "job_order_no": row[5],
                "title": row[6],
                "client_name": row[7],
                "venue_name": row[8],
                "status": row[9],
                "created_date": row[10].strftime("%Y-%m-%d") if row[10] else ""
            })

        return JsonResponse(data, safe=False)

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def get_status_counts(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    (SELECT COUNT(*) FROM public.temp WHERE status = 'Quotation') AS quotation_count,
                    (SELECT COUNT(*) FROM public.temp WHERE status = 'Proforma') AS proforma_count,
                    (SELECT COUNT(*) FROM public.temp WHERE status = 'Prepsheet') AS prepsheet_count,
                    (SELECT COUNT(*) FROM public.jobs WHERE status = 'Delivery Challan') AS deliveryChallan_count,
                    (SELECT COUNT(*) FROM public.jobs) AS job_count
            """)

            row = cursor.fetchone()

        return JsonResponse({
            "quotation_count": row[0] or 0,
            "proforma_count": row[1] or 0,
            "prepsheet_count": row[2] or 0,
            "deliveryChallan_count": row[3] or 0,
            "job_count": row[4] or 0
        })

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def update_jobs(request, id):
    if request.method != 'POST':
        return JsonResponse({
            'error': 'Invalid request method'
        }, status=405)

    job_reference_no = request.POST.get('jobReferenceNo')
    title = request.POST.get('title')
    status = request.POST.get('status')

    try:
        with connection.cursor() as cursor:
            cursor.callproc(
                'jobs_master_list',
                [
                    'UPDATE',
                    id,
                    job_reference_no,
                    title,
                    None,
                    None,
                    None,
                    None,
                    status,
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
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None
                ]
            )

            updated_jobs_id = cursor.fetchone()

        return JsonResponse({
            'message': 'Jobs details updated successfully',
            'updated_jobs_id': updated_jobs_id
        })

    except Exception as e:
        return JsonResponse({
            'error': 'Failed to update jobs details',
            'exception': str(e)
        }, status=500)


@csrf_exempt
def check_equipment_in_temp(request):
    if request.method != 'POST':
        return JsonResponse({
            'error': 'Invalid request method'
        }, status=405)

    title = request.POST.get('title')
    equipment_id = request.POST.get('equipment_name')

    if not equipment_id or not title:
        return JsonResponse({
            'error': 'Both equipment ID and job reference number are required.'
        }, status=400)

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT equipment_name
            FROM public.equipment_list
            WHERE id = %s
        """, [equipment_id])

        equipment_name_result = cursor.fetchone()

        if not equipment_name_result:
            return JsonResponse({
                'error': 'Invalid equipment ID.'
            }, status=400)

        equipment_name = equipment_name_result[0]

        cursor.execute("""
            SELECT 1
            FROM public.temp
            WHERE equipment_name = %s
              AND title = %s
        """, [equipment_name, title])

        exists = cursor.fetchone()

    return JsonResponse({
        'exists': bool(exists),
        'equipment_name': equipment_name
    })

def fetch_client_name(request):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT DISTINCT type, individual_name, company_name
            FROM public.connects
            WHERE individual_name IS NOT NULL
               OR company_name IS NOT NULL
            ORDER BY company_name, individual_name
        """)

        client_names = []

        for row in cursor.fetchall():
            client_type, individual_name, company_name = row

            if company_name:
                client_names.append({
                    "type": client_type,
                    "name": company_name
                })

            if individual_name:
                client_names.append({
                    "type": client_type,
                    "name": individual_name
                })

    return JsonResponse({
        "client_names": client_names
    })

def fetch_individual_names(request):
    client_name = request.GET.get("client_name", "").strip()

    if not client_name:
        return JsonResponse({"individual_names": []})

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT individual_name, mobile_no
            FROM public.connects
            WHERE company = %s
               OR company_name = %s
               OR individual_name = %s
            ORDER BY individual_name
        """, [client_name, client_name, client_name])

        rows = cursor.fetchall()

    return JsonResponse({
        "individual_names": [
            {
                "name": row[0],
                "mobile": row[1]
            }
            for row in rows
            if row[0]
        ]
    })

def get_crew_designations(request):
    return JsonResponse({
        "designations": ["Sound Engineer", "Light Engineer", "Technician", "Helper"]
    })


def get_vehicle_numbers(request):
    return JsonResponse({
        "vehicles": []
    })


def get_driver_list(request):
    return JsonResponse({
        "success": True
    })

