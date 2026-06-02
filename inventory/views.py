import traceback
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
from django.views.decorators.http import require_POST
from django.db import IntegrityError
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



def module_list(request):
    data = []

    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    module_id,
                    module_name
                FROM module_master
                ORDER BY module_name
            """)

            rows = cursor.fetchall()

            for row in rows:
                data.append({
                    "module_id": row[0],
                    "module_name": row[1]
                })

    except Exception as e:
        print("MODULE ERROR:", e)

    return JsonResponse({
        "status": 1,
        "data": data
    })


def add_user(request):
    session_username = request.session.get("username")

    if request.method == "POST":
        user_name = request.POST.get("username", "").strip()
        emp_id = request.POST.get("emp_id", "").strip()
        password = request.POST.get("password", "").strip()
        status = request.POST.get("status") == "1"
        permissions_raw = request.POST.get("permissions", "[]")
        created_by = request.session.get("user_id") or 1

        if not user_name:
            return JsonResponse({
                "success": False,
                "message": "Username is required."
            }, status=400)

        if not emp_id:
            return JsonResponse({
                "success": False,
                "message": "Employee is required."
            }, status=400)

        if not password:
            return JsonResponse({
                "success": False,
                "message": "Password is required."
            }, status=400)

        try:
            emp_id = int(emp_id)
        except Exception:
            return JsonResponse({
                "success": False,
                "message": "Invalid Employee ID."
            }, status=400)

        try:
            permissions = json.loads(permissions_raw) if permissions_raw else []
        except json.JSONDecodeError:
            return JsonResponse({
                "success": False,
                "message": "Invalid permissions JSON."
            }, status=400)

        try:
            with connection.cursor() as cursor:

                cursor.execute("""
                    SELECT user_id
                    FROM public.user_master
                    WHERE LOWER(TRIM(user_name)) = LOWER(TRIM(%s))
                    LIMIT 1
                """, [user_name])

                existing_username = cursor.fetchone()

                if existing_username:
                    return JsonResponse({
                        "success": False,
                        "message": f"User '{user_name}' already exists."
                    }, status=400)

                cursor.execute("""
                    SELECT user_id, user_name
                    FROM public.user_master
                    WHERE emp_id = %s
                    LIMIT 1
                """, [emp_id])

                existing_emp = cursor.fetchone()

                if existing_emp:
                    return JsonResponse({
                        "success": False,
                        "message": f"This employee is already assigned to user '{existing_emp[1]}'."
                    }, status=400)

            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("""
                        SELECT manage_user(
                            %s, %s, %s, %s, %s, %s::jsonb, %s, %s
                        )
                    """, [
                        "create",
                        None,
                        user_name,
                        password,
                        status,
                        json.dumps(permissions),
                        int(created_by),
                        emp_id
                    ])

                    row = cursor.fetchone()
                    user_id = row[0] if row else None

            return JsonResponse({
                "success": True,
                "message": f"User {user_name} added successfully.",
                "user_id": user_id
            })

        except Exception as e:
            print("ADD USER ERROR:", str(e))
            return JsonResponse({
                "success": False,
                "message": str(e)
            }, status=500)

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT id, name
            FROM public.employee
            ORDER BY name
        """)
        employees = cursor.fetchall()

    employee_data = [
        {"id": row[0], "name": row[1]}
        for row in employees
    ]

    return render(request, "inventory/User.html", {
        "employee_data": employee_data,
        "username": session_username
    })


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
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request method"}, status=405)

    form_user_id = request.POST.get("userId") or user_id
    user_name = request.POST.get("username", "").strip()
    password = request.POST.get("password", "").strip()
    status = request.POST.get("status") == "1"
    emp_id = request.POST.get("emp_id", "").strip()
    permissions_raw = request.POST.get("permissions", "[]")

    try:
        form_user_id = int(form_user_id)
        emp_id = int(emp_id)
    except Exception:
        return JsonResponse({
            "success": False,
            "message": "Invalid user or employee ID."
        }, status=400)

    try:
        permissions = json.loads(permissions_raw) if permissions_raw else []
    except json.JSONDecodeError:
        return JsonResponse({
            "success": False,
            "message": "Invalid permissions JSON."
        }, status=400)

    try:
        with connection.cursor() as cursor:

            cursor.execute("""
                SELECT user_id
                FROM public.user_master
                WHERE LOWER(TRIM(user_name)) = LOWER(TRIM(%s))
                  AND user_id <> %s
                LIMIT 1
            """, [user_name, form_user_id])

            existing_username = cursor.fetchone()

            if existing_username:
                return JsonResponse({
                    "success": False,
                    "message": f"User '{user_name}' already exists."
                }, status=400)

            cursor.execute("""
                SELECT user_id, user_name
                FROM public.user_master
                WHERE emp_id = %s
                  AND user_id <> %s
                LIMIT 1
            """, [emp_id, form_user_id])

            existing_emp = cursor.fetchone()

            if existing_emp:
                return JsonResponse({
                    "success": False,
                    "message": f"This employee is already assigned to user '{existing_emp[1]}'."
                }, status=400)

            cursor.execute("""
                SELECT manage_user(
                    %s, %s, %s, %s, %s, %s::jsonb, %s, %s
                )
            """, [
                "update",
                form_user_id,
                user_name,
                password,
                status,
                json.dumps(permissions),
                None,
                emp_id
            ])

        return JsonResponse({
            "success": True,
            "message": "User details updated successfully",
            "user_id": form_user_id
        })

    except Exception as e:
        print("UPDATE USER ERROR:", str(e))
        return JsonResponse({
            "success": False,
            "message": str(e)
        }, status=500)


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
        cursor.execute("""
            SELECT id, name
            FROM public.employee
            WHERE status = true
            ORDER BY name
        """)
        return [
            {'id': row[0], 'name': row[1]}
            for row in cursor.fetchall()
        ]


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

                    e.reporting AS reporting_id,
                    COALESCE(r.name, '') AS reporting_name,

                    e.p_address,
                    e.c_address,
                    e.country,
                    e.state,
                    e.blood_group,
                    e.status,

                    COALESCE(
                        json_agg(ei.images)
                        FILTER (WHERE ei.images IS NOT NULL),
                        '[]'
                    ) AS images

                FROM public.employee e

                LEFT JOIN public.employee r
                    ON r.id::text = e.reporting::text

                LEFT JOIN public.employee_images ei
                    ON ei.employee_id = e.id

                GROUP BY
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
                    r.name,
                    e.p_address,
                    e.c_address,
                    e.country,
                    e.state,
                    e.blood_group,
                    e.status

                ORDER BY e.id DESC
            """)

            rows = cursor.fetchall()

        data = []

        for row in rows:
            data.append({
                "id": row[0],
                "employee_id": row[1] or "",
                "employee_type": row[2] or "",
                "name": row[3] or "",
                "email": row[4] or "",
                "mobile_no": row[5] or "",
                "designation": row[6] or "",
                "gender": row[7] or "",
                "joining_date": row[8].strftime("%Y-%m-%d") if row[8] else "",
                "dob": row[9].strftime("%Y-%m-%d") if row[9] else "",

                # Reporting Fix
                "reporting_id": row[10] or "",
                "reporting": row[11] or "",

                "p_address": row[12] or "",
                "c_address": row[13] or "",
                "country": row[14] or "",
                "state": row[15] or "",
                "blood_group": row[16] or "",
                "status": row[17] or "Active",

                "images": row[18] if row[18] else []
            })

        return JsonResponse({
            "success": True,
            "data": data
        })

    except Exception as e:
        print("EMPLOYEE LIST ERROR:", str(e))

        return JsonResponse({
            "success": False,
            "data": [],
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
            cursor.execute("""
                SELECT
                    el.id,
                    el.equipment_name,
                    sc.name AS sub_category_name,
                    el.category_type,
                    COALESCE(um.user_name, '') AS created_by,
                    el.created_date,
                    el.dimension_height,
                    el.dimension_width,
                    el.dimension_length,
                    el.hsn_no,
                    COALESCE(ea.image_1, '') AS image_1,
                    COALESCE(ea.image_2, '') AS image_2,
                    COALESCE(ea.image_3, '') AS image_3
                FROM public.equipment_list el
                LEFT JOIN public.sub_category sc ON sc.id = el.sub_category_id
                LEFT JOIN public.user_master um ON um.user_id = el.created_by
                LEFT JOIN public.equipment_list_attachments ea ON ea.equipment_list_id = el.id
                ORDER BY el.id DESC
            """)

            for row in cursor.fetchall():
                equipment_listing.append({
                    'id': row[0],
                    'equipment_name': row[1],
                    'sub_category_name': row[2],
                    'category_type': row[3],
                    'created_by': row[4],
                    'created_date': row[5].strftime('%d-%m-%Y') if row[5] else '',
                    'dimension_height': row[6] or '',
                    'dimension_width': row[7] or '',
                    'dimension_length': row[8] or '',
                    'hsn_no': row[9] or '',
                    'image_1': row[10] or '',
                    'image_2': row[11] or '',
                    'image_3': row[12] or '',
                })

            cursor.execute("""
                SELECT
                    sc.id,
                    mc.category_name,
                    sc.name
                FROM public.sub_category sc
                JOIN public.master_category mc ON mc.category_id = sc.category_id
                ORDER BY sc.name
            """)

            subcategories = [
                {'id': row[0], 'category_name': row[1], 'name': row[2]}
                for row in cursor.fetchall()
            ]

    except Exception as e:
        print("ASSET ENTRY ERROR:", e)

    return render(request, 'inventory/asset_creation.html', {
        'equipment_listing': equipment_listing,
        'subcategories': subcategories,
        'username': username
    })


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


def save_uploaded_file(uploaded_file, folder_name="equipment"):
    if not uploaded_file:
        return None

    folder_path = os.path.join(settings.MEDIA_ROOT, folder_name)
    os.makedirs(folder_path, exist_ok=True)

    file_name = uploaded_file.name
    file_path = os.path.join(folder_path, file_name)

    with open(file_path, "wb+") as destination:
        for chunk in uploaded_file.chunks():
            destination.write(chunk)

    return os.path.join(settings.MEDIA_URL, folder_name, file_name)


def add_equipment(request):
    if request.method != 'POST':
        return JsonResponse({
            'success': False,
            'message': 'Invalid request method'
        })

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

    if not equipment_name:
        return JsonResponse({
            'success': False,
            'message': 'Equipment name is required.'
        })

    if not subcategory_id:
        return JsonResponse({
            'success': False,
            'message': 'Subcategory is required.'
        })

    try:
        subcategory_id = int(subcategory_id)
    except (ValueError, TypeError):
        return JsonResponse({
            'success': False,
            'message': 'Invalid subcategory value.'
        })

    image_1 = save_uploaded_file(request.FILES.get('attachment_1'), "equipment")
    image_2 = save_uploaded_file(request.FILES.get('attachment_2'), "equipment")
    image_3 = save_uploaded_file(request.FILES.get('attachment_3'), "equipment")

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
                    %s::varchar,
                    %s::varchar,
                    %s::varchar,
                    %s::integer,
                    %s::timestamp,
                    %s::varchar,
                    %s::varchar,
                    %s::varchar
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
                status,
                created_by,
                created_date,
                image_1,
                image_2,
                image_3
            ])

            equipment_id = cursor.fetchone()[0]

        return JsonResponse({
            'success': True,
            'message': 'Equipment added successfully.',
            'equipment_id': equipment_id
        })

    except IntegrityError as e:
        error_message = str(e)

        if (
            'equipment_list_name_unique' in error_message
            or 'equipment_list_name_subcategory_unique' in error_message
            or 'unique_equipment_name' in error_message
            or 'equipment_list_equipment_name_key' in error_message
        ):
            return JsonResponse({
                'success': False,
                'message': 'Equipment name already exists.'
            })

        return JsonResponse({
            'success': False,
            'message': 'Duplicate equipment record found.'
        })

    except Exception as e:
        error_message = str(e)

        if (
            'equipment_list_name_unique' in error_message
            or 'equipment_list_name_subcategory_unique' in error_message
            or 'duplicate key value violates unique constraint' in error_message
        ):
            return JsonResponse({
                'success': False,
                'message': 'Equipment name already exists.'
            })

        print("ADD EQUIPMENT ERROR:", error_message)

        return JsonResponse({
            'success': False,
            'message': error_message
        }, status=500)


def equipment_list(request):
    try:
        with connection.cursor() as cursor:

            cursor.execute("""
                SELECT
                    el.id,
                    el.equipment_name,
                    sc.name AS sub_category_name,
                    el.category_type,
                    COALESCE(um.user_name, '') AS created_by,
                    el.created_date,
                    el.dimension_height,
                    el.dimension_width,
                    el.dimension_length,
                    el.hsn_no,
                    COALESCE(ea.image_1, '') AS image_1,
                    COALESCE(ea.image_2, '') AS image_2,
                    COALESCE(ea.image_3, '') AS image_3
                FROM public.equipment_list el
                LEFT JOIN public.sub_category sc
                    ON sc.id = el.sub_category_id
                LEFT JOIN public.user_master um
                    ON um.user_id = el.created_by
                LEFT JOIN public.equipment_list_attachments ea
                    ON ea.equipment_list_id = el.id
                ORDER BY el.id DESC
            """)

            rows = cursor.fetchall()

            equipment_listing = []

            for row in rows:
                equipment_listing.append({
                    'id': row[0],
                    'equipment_name': row[1],
                    'sub_category_name': row[2],
                    'category_type': row[3],
                    'created_by': row[4],
                    'created_date': row[5].strftime('%d-%m-%Y') if row[5] else '',
                    'dimension_height': row[6] or '',
                    'dimension_width': row[7] or '',
                    'dimension_length': row[8] or '',
                    'hsn_no': row[9] or '',
                    'image_1': row[10] or '',
                    'image_2': row[11] or '',
                    'image_3': row[12] or '',
                })

        return JsonResponse({
            'equipments': equipment_listing
        })

    except Exception as e:
        print("Equipment List Error:", str(e))
        return JsonResponse({
            'equipments': [],
            'error': str(e)
        })

@csrf_exempt
def insert_vendor(request):
    if request.method != 'POST':
        return JsonResponse({
            'success': False,
            'message': 'Invalid request method'
        }, status=405)

    try:
        equipment_id = request.POST.get('equipmentId')
        warehouse_id = request.POST.get('warehouse_id')
        vendor_name = request.POST.get('vendor_name')
        purchase_date = request.POST.get('purchase_date')
        unit_price = request.POST.get('unit_price')
        rental_price = request.POST.get('rental_price')
        reference_no = request.POST.get('reference_no')
        unit = request.POST.get('unitValue')

        if not equipment_id:
            return JsonResponse({
                'success': False,
                'message': 'Equipment ID missing. Please click Add Stock again.'
            }, status=400)

        if not warehouse_id:
            return JsonResponse({
                'success': False,
                'message': 'Warehouse is required.'
            }, status=400)

        if not unit:
            return JsonResponse({
                'success': False,
                'message': 'Unit is required.'
            }, status=400)

        equipment_id = int(equipment_id)
        warehouse_id = int(warehouse_id)
        unit = int(unit)

        serial_numbers = []
        barcode_numbers = []

        for i in range(1, unit + 1):
            serial_numbers.append(
                request.POST.get(f'serialNumber{i}', '').strip()
            )
            barcode_numbers.append(
                request.POST.get(f'barcodeNumber{i}', '').strip()
            )

        attachment_path = save_uploaded_file(
            request.FILES.get('attachment'),
            "stock"
        )

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT public.add_stock(
                    %s::integer,
                    %s::varchar,
                    %s::date,
                    %s::numeric,
                    %s::numeric,
                    %s::varchar,
                    %s::varchar,
                    %s::integer,
                    %s::text[],
                    %s::text[],
                    %s::integer
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
                warehouse_id
            ])

        return JsonResponse({
            'success': True,
            'message': 'Stock added successfully.'
        })

    except IntegrityError as e:
        error_message = str(e)

        if 'stock_details_serial_no_unique' in error_message:
            return JsonResponse({
                'success': False,
                'message': 'Serial number already exists.'
            }, status=400)

        if 'stock_details_barcode_no_unique' in error_message:
            return JsonResponse({
                'success': False,
                'message': 'Barcode number already exists.'
            }, status=400)

        return JsonResponse({
            'success': False,
            'message': 'Duplicate stock record found.'
        }, status=400)

    except Exception as e:
        error_message = str(e)
        print("INSERT STOCK ERROR:", error_message)

        return JsonResponse({
            'success': False,
            'message': error_message
        }, status=500)

def subcategory_dropdown(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    sc.id,
                    mc.category_name,
                    sc.name
                FROM public.sub_category sc
                JOIN public.master_category mc
                    ON mc.category_id = sc.category_id
                WHERE sc.status = TRUE
                ORDER BY sc.name
            """)
            subcategories = [
                {'id': row[0], 'category_name': row[1], 'name': row[2]}
                for row in cursor.fetchall()
            ]
        return JsonResponse({'subcategories': subcategories})
    except Exception as e:
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


def get_equipment_details(request, equipment_id):
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
                    COALESCE(ea.image_1, '') AS image_1,
                    COALESCE(ea.image_2, '') AS image_2,
                    COALESCE(ea.image_3, '') AS image_3
                FROM public.equipment_list el
                LEFT JOIN public.sub_category sc ON sc.id = el.sub_category_id
                LEFT JOIN public.equipment_list_attachments ea ON ea.equipment_list_id = el.id
                WHERE el.id = %s
            """, [equipment_id])

            row = cursor.fetchone()

        if not row:
            return JsonResponse({'success': False, 'message': 'Equipment not found'}, status=404)

        return JsonResponse({
            'success': True,
            'id': row[0],
            'equipment_name': row[1],
            'subcategory_id': row[2],
            'subcategory_name': row[3],
            'category_name': row[4],
            'dimension_h': row[5],
            'dimension_w': row[6],
            'dimension_l': row[7],
            'weight': row[8],
            'volume': row[9],
            'hsn_no': row[10],
            'country_origin': row[11],
            'status': 'Active' if row[12] else 'Inactive',
            'image_1': row[13],
            'image_2': row[14],
            'image_3': row[15],
        })

    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)

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
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    cm.crew_id,
                    cm.crew_designation,
                    cm.status,
                    COALESCE(um.user_name, '-') AS created_by_name,
                    cm.created_date
                FROM public.crew_master cm
                LEFT JOIN public.user_master um
                    ON um.user_id = cm.created_by
                WHERE cm.status = true
                ORDER BY cm.crew_id DESC
            """)

            rows = cursor.fetchall()

        data = []

        for row in rows:
            data.append({
                "crew_id": row[0],
                "crew_designation": row[1],
                "status": row[2],
                "created_by": row[3],
                "created_date": row[4].strftime("%Y-%m-%d %H:%M:%S") if row[4] else ""
            })

        return JsonResponse({"data": data})

    except Exception as e:
        print("CREW LIST ERROR:", str(e))
        return JsonResponse({"data": [], "error": str(e)}, status=500)


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

def create_co_jobs(cursor, job_id, main_job_no, created_by, co_jobs_json=None):
    split_map = {}

    print("CREATE CO JOB INPUT:", co_jobs_json)

    if not co_jobs_json:
        return split_map

    try:
        co_jobs = json.loads(co_jobs_json)
    except Exception as e:
        print("CO JOB JSON ERROR:", str(e))
        return split_map

    print("CO JOB COUNT:", len(co_jobs))

    for item in co_jobs:
        local_id = str(item.get("local_id") or "").strip()
        split_id = item.get("split_id")
        section_name = (item.get("section_name") or "").strip()

        if not section_name:
            continue

        location = (item.get("location") or "").strip()
        incharge = (item.get("incharge") or "").strip()
        rehearsal_date = item.get("rehearsal_date") or None
        event_date = item.get("event_date") or None
        status = item.get("status") or "Draft"

        if split_id and str(split_id).isdigit():
            cursor.execute("""
                UPDATE public.job_split_master
                SET section_name = %s,
                    location = %s,
                    incharge = %s,
                    rehearsal_date = %s,
                    event_date = %s,
                    status = %s
                WHERE split_id = %s AND parent_job_id = %s
            """, [
                section_name, location, incharge,
                rehearsal_date, event_date, status,
                int(split_id), job_id
            ])

            split_map[str(split_id)] = int(split_id)
            split_map[local_id] = int(split_id)
            continue

        cursor.execute("SELECT public.generate_co_job_no(%s)", [job_id])
        split_job_no = cursor.fetchone()[0]

        cursor.execute("""
            INSERT INTO public.job_split_master (
                parent_job_id,
                split_job_no,
                section_name,
                status,
                created_by,
                created_date,
                location,
                incharge,
                rehearsal_date,
                event_date
            )
            VALUES (%s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s)
            RETURNING split_id
        """, [
            job_id,
            split_job_no,
            section_name,
            status,
            created_by,
            location,
            incharge,
            rehearsal_date,
            event_date
        ])

        new_split_id = cursor.fetchone()[0]

        split_map[local_id] = new_split_id
        split_map[str(new_split_id)] = new_split_id

        print("CO JOB INSERTED:", split_job_no, new_split_id)

    return split_map

def job_sections(request, job_id):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT
                split_id,
                split_job_no,
                section_name
            FROM public.job_split_master
            WHERE parent_job_id = %s
            ORDER BY split_id
        """, [job_id])

        rows = cursor.fetchall()

    data = []

    for row in rows:
        data.append({
            "split_id": row[0],
            "split_job_no": row[1],
            "section_name": row[2],
        })

    return JsonResponse(data, safe=False)

def insert_job_child_rows_direct(
    cursor,
    job_id,
    split_map,
    equipment_location,
    equipment_incharge,
    equipment_event_date,
    equipment_rehearsal_date,
    equipment_split_ids,
    equipment_categories,
    equipment_sub_categories,
    equipment_ids,
    equipment_qtys,
    rental_prices,
    equipment_totals,
    equipment_notes,
    crew_types,
    crew_days,
    perday_charges,
    crew_totals,
    crew_notes,
    vendor_names,
    sub_equipment_names,
    sub_quantities,
    sub_unit_prices,
    sub_totals,
    transport_amounts,
    driver_names,
    contact_numbers,
    vehicle_numbers,
    outside_driver_names,
    outside_contact_numbers,
    outside_vehicle_numbers
):
    for i, equipment_id in enumerate(equipment_ids):
        if not equipment_id:
            continue

        row_split_id = equipment_split_ids[i] if i < len(equipment_split_ids) else None

        if row_split_id:
            row_split_id = str(row_split_id).strip()

        if row_split_id and row_split_id.startswith("local_"):
            row_split_id = split_map.get(row_split_id)
        elif row_split_id and row_split_id.isdigit():
            row_split_id = int(row_split_id)
        else:
            row_split_id = None

        cursor.execute("""
            SELECT equipment_name
            FROM public.equipment_list
            WHERE id = %s
        """, [equipment_id])

        row = cursor.fetchone()
        equipment_name = row[0] if row else ""

        cursor.execute("""
            INSERT INTO public.job_equipment_details (
                job_id,
                equipment_detail_id,
                location,
                incharge,
                equipment_setup_date,
                equipment_rehearsal_date,
                equipment_name,
                quantity,
                equipment_unit_price,
                equipment_total,
                equipment_notes,
                assign_status,
                category_name,
                sub_category_name,
                split_id
            )
            VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, false, %s, %s, %s
            )
        """, [
            job_id,
            equipment_id,
            equipment_location,
            equipment_incharge,
            equipment_event_date,
            equipment_rehearsal_date,
            equipment_name,
            equipment_qtys[i] if i < len(equipment_qtys) else "",
            rental_prices[i] if i < len(rental_prices) else "",
            equipment_totals[i] if i < len(equipment_totals) else "",
            equipment_notes[i] if i < len(equipment_notes) else "",
            equipment_categories[i] if i < len(equipment_categories) else "",
            equipment_sub_categories[i] if i < len(equipment_sub_categories) else "",
            row_split_id
        ])

    for i, crew_type in enumerate(crew_types):
        if not crew_type:
            continue

        days = crew_days[i] if i < len(crew_days) else "0"
        per_day = perday_charges[i] if i < len(perday_charges) else "0"
        total = crew_totals[i] if i < len(crew_totals) else ""

        if not total:
            try:
                total = float(days or 0) * float(per_day or 0)
            except Exception:
                total = 0

        cursor.execute("""
            INSERT INTO public.job_crew_allocation (
                job_id,
                crew_type,
                emp_id,
                crew_no_of_days,
                perday_charges,
                total,
                crew_notes
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, [
            job_id,
            crew_type,
            None,
            days,
            per_day,
            total,
            crew_notes[i] if i < len(crew_notes) else ""
        ])

    for i, vendor_name in enumerate(vendor_names):
        if not vendor_name and not (i < len(sub_equipment_names) and sub_equipment_names[i]):
            continue

        cursor.execute("""
            INSERT INTO public.job_sub_vendor (
                job_id,
                vendor_name,
                equipment_name,
                quantity,
                unit_price,
                total
            )
            VALUES (%s, %s, %s, %s, %s, %s)
        """, [
            job_id,
            vendor_name,
            sub_equipment_names[i] if i < len(sub_equipment_names) else "",
            sub_quantities[i] if i < len(sub_quantities) else "",
            sub_unit_prices[i] if i < len(sub_unit_prices) else "",
            sub_totals[i] if i < len(sub_totals) else ""
        ])

    max_transport = max(
        len(driver_names),
        len(outside_driver_names),
        len(vehicle_numbers),
        len(outside_vehicle_numbers),
        len(transport_amounts),
        0
    )

    for i in range(max_transport):
        driver_name = driver_names[i] if i < len(driver_names) else ""
        contact_number = contact_numbers[i] if i < len(contact_numbers) else ""
        vehicle_number = vehicle_numbers[i] if i < len(vehicle_numbers) else ""

        outside_driver = outside_driver_names[i] if i < len(outside_driver_names) else ""
        outside_contact = outside_contact_numbers[i] if i < len(outside_contact_numbers) else ""
        outside_vehicle = outside_vehicle_numbers[i] if i < len(outside_vehicle_numbers) else ""

        amount = transport_amounts[i] if i < len(transport_amounts) else ""

        if not any([driver_name, vehicle_number, outside_driver, outside_vehicle, amount]):
            continue

        cursor.execute("""
            INSERT INTO public.job_transportation_allocation (
                job_id,
                driver_name,
                contact_number,
                vehicle_number,
                outside_driver_name,
                outside_contact_number,
                outside_vehicle_number,
                transport_amount
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, [
            job_id,
            driver_name,
            contact_number,
            vehicle_number,
            outside_driver,
            outside_contact,
            outside_vehicle,
            amount or 0
        ])


def add_job(request):
    username = request.session.get("username")

    edit_id = request.GET.get("edit_id") or request.POST.get("edit_id")
    edit_type = request.GET.get("edit_type") or request.POST.get("edit_type")

    job_data = {}
    equipment_data = []
    crew_data = []
    sub_vendor_data = []
    transport_data = []
    split_data = []

    if request.method == "POST":
        try:
            print("=" * 120)
            print("ADD JOB POST DATA")
            print("=" * 120)
            for key, value in request.POST.lists():
                for item in value:
                    print(f"{key} => {item}")
            print("=" * 120)

            title = request.POST.get("title", "").strip()
            venue_name = request.POST.get("venue_name", "").strip()
            venue_address = request.POST.get("venue_address", "").strip()
            client_name = request.POST.get("client_name", "").strip()
            contact_person_name = request.POST.get("contact_person_name", "").strip()
            contact_person_number = request.POST.get("contact_person_number", "").strip()

            setup_date = request.POST.get("setup_date") or None
            rehearsal_date = request.POST.get("rehearsal_date") or None
            start_date = request.POST.get("start_date") or None
            end_date = request.POST.get("end_date") or None

            status = request.POST.get("status", "").strip()
            crew_type = request.POST.get("crew_type", "").strip()
            input_notes = request.POST.get("input_notes", "").strip()

            total_days = request.POST.get("total_days") or "0"
            amount_row = request.POST.get("amount_row") or "0"
            discount = request.POST.get("discount") or "0"
            discounted_amount = request.POST.get("discounted_amount") or "0"
            total_amount = request.POST.get("total_amount") or "0"

            equipment_location = request.POST.get("equipment_location", "").strip()
            equipment_incharge = request.POST.get("equipment_incharge", "").strip()
            equipment_rehearsal_date = request.POST.get("equipment_rehearsal_date") or None
            equipment_event_date = request.POST.get("equipment_event_date") or None

            co_jobs_json = request.POST.get("co_jobs_json", "[]")
            created_by = request.session.get("user_id")

            equipment_split_ids = request.POST.getlist("equipment_split_id[]")
            equipment_categories = request.POST.getlist("equipment_category[]")
            equipment_sub_categories = request.POST.getlist("equipment_sub_category[]")
            equipment_ids = request.POST.getlist("equipment_name[]")
            equipment_qtys = request.POST.getlist("equipment_qty[]")
            rental_prices = request.POST.getlist("rental_price[]")
            equipment_totals = request.POST.getlist("equipment_total[]")
            equipment_notes = request.POST.getlist("equipment_notes[]")

            crew_types = request.POST.getlist("crew_type[]")
            crew_days = request.POST.getlist("crew_no_of_days[]")
            perday_charges = request.POST.getlist("perday_charges[]")
            crew_totals = request.POST.getlist("crew_total[]")
            crew_notes = request.POST.getlist("crew_notes[]")

            vendor_names = request.POST.getlist("vendor-name")
            sub_equipment_names = request.POST.getlist("equipment-name")
            sub_quantities = request.POST.getlist("quantity")
            sub_unit_prices = request.POST.getlist("sub_unit_price")
            sub_totals = request.POST.getlist("sub_total")

            transport_amounts = request.POST.getlist("transport_amount[]")
            driver_names = request.POST.getlist("driver_name[]")
            contact_numbers = request.POST.getlist("contact_number[]")
            vehicle_numbers = request.POST.getlist("vehicle_number[]")
            outside_driver_names = request.POST.getlist("outside_driver_name[]")
            outside_contact_numbers = request.POST.getlist("outside_contact_number[]")
            outside_vehicle_numbers = request.POST.getlist("outside_vehicle_number[]")

            if not title:
                return JsonResponse({
                    "success": False,
                    "error": "Title is required."
                }, status=400)

            if not status:
                return JsonResponse({
                    "success": False,
                    "error": "Status is required."
                }, status=400)

            with transaction.atomic():
                with connection.cursor() as cursor:

                    # =====================================================
                    # EDIT QUOTATION - TEMP
                    # =====================================================
                    if edit_id and edit_type == "temp":
                        edit_id_int = int(edit_id)

                        cursor.execute("""
                            UPDATE public.temp
                            SET
                                title = %s,
                                client_name = %s,
                                contact_person_name = %s,
                                contact_person_number = %s,
                                status = %s,
                                venue_name = %s,
                                venue_address = %s,
                                crew_type = %s,
                                setup_date = %s,
                                rehearsal_date = %s,
                                event_date = %s,
                                dismantle_date = %s,
                                total_days = %s,
                                amount_row = %s,
                                discount = %s,
                                amount_after_discount = %s,
                                total_amount = %s,
                                notes = %s
                            WHERE id = %s
                        """, [
                            title,
                            client_name,
                            contact_person_name,
                            contact_person_number,
                            "Quotation",
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
                            input_notes,
                            edit_id_int
                        ])

                        cursor.execute("DELETE FROM public.temp_equipment_details WHERE temp_id = %s", [edit_id_int])
                        cursor.execute("DELETE FROM public.temp_crew_allocation WHERE temp_id = %s", [edit_id_int])
                        cursor.execute("DELETE FROM public.temp_sub_vendor WHERE temp_id = %s", [edit_id_int])
                        cursor.execute("DELETE FROM public.temp_transportation_allocation WHERE temp_id = %s", [edit_id_int])

                        save_temp_child_rows(
                            cursor,
                            edit_id_int,
                            equipment_location,
                            equipment_incharge,
                            equipment_event_date,
                            equipment_rehearsal_date,
                            equipment_categories,
                            equipment_ids,
                            equipment_qtys,
                            rental_prices,
                            equipment_totals,
                            equipment_notes,
                            crew_types,
                            crew_days,
                            perday_charges,
                            crew_totals,
                            crew_notes,
                            vendor_names,
                            sub_equipment_names,
                            sub_quantities,
                            sub_unit_prices,
                            sub_totals,
                            transport_amounts,
                            driver_names,
                            contact_numbers,
                            vehicle_numbers,
                            outside_driver_names,
                            outside_contact_numbers,
                            outside_vehicle_numbers
                        )

                        return JsonResponse({
                            "success": True,
                            "message": "Quotation updated successfully.",
                            "temp_id": edit_id_int
                        })

                    # =====================================================
                    # EDIT JOB - PROFORMA/PREPSHEET/DC
                    # =====================================================
                    if edit_id and edit_type == "job":
                        job_id = int(edit_id)

                        cursor.execute("""
                            UPDATE public.jobs
                            SET
                                title = %s,
                                client_name = %s,
                                contact_person_name = %s,
                                contact_person_number = %s,
                                status = %s,
                                venue_name = %s,
                                venue_address = %s,
                                crew_type = %s,
                                setup_date = %s,
                                rehearsal_date = %s,
                                show_start_date = %s,
                                show_end_date = %s,
                                total_days = %s,
                                amount_row = %s,
                                discount = %s,
                                amount_after_discount = %s,
                                total_amount = %s,
                                notes = %s
                            WHERE id = %s
                        """, [
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
                            input_notes,
                            job_id
                        ])

                        cursor.execute("""
                            SELECT COALESCE(main_job_no, job_order_no, job_reference_no)
                            FROM public.jobs
                            WHERE id = %s
                        """, [job_id])
                        main_job_no = cursor.fetchone()[0]

                        split_map = create_co_jobs(
                            cursor=cursor,
                            job_id=job_id,
                            main_job_no=main_job_no,
                            created_by=created_by,
                            co_jobs_json=co_jobs_json
                        )

                        cursor.execute("DELETE FROM public.job_equipment_details WHERE job_id = %s", [job_id])
                        cursor.execute("DELETE FROM public.job_crew_allocation WHERE job_id = %s", [job_id])
                        cursor.execute("DELETE FROM public.job_sub_vendor WHERE job_id = %s", [job_id])
                        cursor.execute("DELETE FROM public.job_transportation_allocation WHERE job_id = %s", [job_id])

                        insert_job_child_rows_direct(
                            cursor,
                            job_id,
                            split_map,
                            equipment_location,
                            equipment_incharge,
                            equipment_event_date,
                            equipment_rehearsal_date,
                            equipment_split_ids,
                            equipment_categories,
                            equipment_sub_categories,
                            equipment_ids,
                            equipment_qtys,
                            rental_prices,
                            equipment_totals,
                            equipment_notes,
                            crew_types,
                            crew_days,
                            perday_charges,
                            crew_totals,
                            crew_notes,
                            vendor_names,
                            sub_equipment_names,
                            sub_quantities,
                            sub_unit_prices,
                            sub_totals,
                            transport_amounts,
                            driver_names,
                            contact_numbers,
                            vehicle_numbers,
                            outside_driver_names,
                            outside_contact_numbers,
                            outside_vehicle_numbers
                        )

                        return JsonResponse({
                            "success": True,
                            "message": f"{status} updated successfully.",
                            "job_id": job_id
                        })

                    # =====================================================
                    # ADD NEW QUOTATION - TEMP
                    # =====================================================
                    if status == "Quotation":
                        quotation_no = generate_quotation_no()

                        cursor.execute("""
                            INSERT INTO public.temp (
                                job_reference_no,
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
                                event_date,
                                dismantle_date,
                                total_days,
                                amount_row,
                                discount,
                                amount_after_discount,
                                total_amount,
                                created_by,
                                created_date,
                                notes,
                                quotation_no
                            )
                            VALUES (
                                %s, %s, %s, %s, %s,
                                'Quotation', %s, %s, %s, %s,
                                %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, NOW(),
                                %s, %s
                            )
                            RETURNING id
                        """, [
                            quotation_no,
                            title,
                            client_name,
                            contact_person_name,
                            contact_person_number,
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
                            input_notes,
                            quotation_no
                        ])

                        temp_id = cursor.fetchone()[0]

                        save_temp_child_rows(
                            cursor,
                            temp_id,
                            equipment_location,
                            equipment_incharge,
                            equipment_event_date,
                            equipment_rehearsal_date,
                            equipment_categories,
                            equipment_ids,
                            equipment_qtys,
                            rental_prices,
                            equipment_totals,
                            equipment_notes,
                            crew_types,
                            crew_days,
                            perday_charges,
                            crew_totals,
                            crew_notes,
                            vendor_names,
                            sub_equipment_names,
                            sub_quantities,
                            sub_unit_prices,
                            sub_totals,
                            transport_amounts,
                            driver_names,
                            contact_numbers,
                            vehicle_numbers,
                            outside_driver_names,
                            outside_contact_numbers,
                            outside_vehicle_numbers
                        )

                        return JsonResponse({
                            "success": True,
                            "message": "Quotation saved successfully.",
                            "quotation_no": quotation_no,
                            "temp_id": temp_id
                        })

                    # =====================================================
                    # ADD DIRECT JOB - PROFORMA/PREPSHEET/DC
                    # =====================================================
                    cursor.execute("SELECT public.generate_job_no()")
                    job_no = cursor.fetchone()[0]

                    cursor.execute("""
                        INSERT INTO public.jobs (
                            job_reference_no,
                            quotation_no,
                            main_job_no,
                            job_order_no,
                            title,
                            client_name,
                            contact_person_name,
                            contact_person_number,
                            venue_name,
                            venue_address,
                            status,
                            crew_type,
                            setup_date,
                            rehearsal_date,
                            show_start_date,
                            show_end_date,
                            total_days,
                            amount_row,
                            discount,
                            amount_after_discount,
                            total_amount,
                            created_by,
                            created_date,
                            notes
                        )
                        VALUES (
                            %s, NULL, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s, NOW(), %s
                        )
                        RETURNING id
                    """, [
                        job_no,
                        job_no,
                        job_no,
                        title,
                        client_name,
                        contact_person_name,
                        contact_person_number,
                        venue_name,
                        venue_address,
                        status,
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

                    job_id = cursor.fetchone()[0]

                    split_map = create_co_jobs(
                        cursor=cursor,
                        job_id=job_id,
                        main_job_no=job_no,
                        created_by=created_by,
                        co_jobs_json=co_jobs_json
                    )

                    insert_job_child_rows_direct(
                        cursor,
                        job_id,
                        split_map,
                        equipment_location,
                        equipment_incharge,
                        equipment_event_date,
                        equipment_rehearsal_date,
                        equipment_split_ids,
                        equipment_categories,
                        equipment_sub_categories,
                        equipment_ids,
                        equipment_qtys,
                        rental_prices,
                        equipment_totals,
                        equipment_notes,
                        crew_types,
                        crew_days,
                        perday_charges,
                        crew_totals,
                        crew_notes,
                        vendor_names,
                        sub_equipment_names,
                        sub_quantities,
                        sub_unit_prices,
                        sub_totals,
                        transport_amounts,
                        driver_names,
                        contact_numbers,
                        vehicle_numbers,
                        outside_driver_names,
                        outside_contact_numbers,
                        outside_vehicle_numbers
                    )

                    return JsonResponse({
                        "success": True,
                        "message": f"{status} saved successfully.",
                        "job_id": job_id,
                        "job_no": job_no
                    })

        except Exception as e:
            print("ADD_JOB ERROR:", str(e))
            return JsonResponse({
                "success": False,
                "error": str(e)
            }, status=500)

    # =====================================================
    # GET EDIT DATA
    # =====================================================
    if request.method == "GET" and edit_id and edit_type:
        try:
            with connection.cursor() as cursor:
                if edit_type == "job":
                    cursor.execute("SELECT * FROM public.jobs WHERE id = %s", [edit_id])
                    columns = [col[0] for col in cursor.description]
                    row = cursor.fetchone()
                    job_data = dict(zip(columns, row)) if row else {}

                    cursor.execute("""
                        SELECT
                            jed.id,
                            jed.job_id,
                            jed.split_id,
                            NULLIF(jed.equipment_detail_id, '')::int AS equipment_id,
                            COALESCE(el.equipment_name, jed.equipment_name, '') AS equipment_name,
                            COALESCE(mc.category_name, jed.category_name, '') AS category_name,
                            COALESCE(sc.name, jed.sub_category_name, '') AS sub_category_name,
                            COALESCE(NULLIF(jed.quantity, '')::numeric, 0) AS equipment_qty,
                            COALESCE(NULLIF(jed.equipment_unit_price, '')::numeric, 0) AS rental_price,
                            COALESCE(NULLIF(jed.equipment_total, '')::numeric, 0) AS equipment_total,
                            COALESCE(jed.equipment_notes, '') AS equipment_notes,
                            COALESCE(stock.available_qty, 0) AS available_qty
                        FROM public.job_equipment_details jed
                        LEFT JOIN public.equipment_list el
                            ON el.id = NULLIF(jed.equipment_detail_id, '')::int
                        LEFT JOIN public.sub_category sc
                            ON sc.id = el.sub_category_id
                        LEFT JOIN public.master_category mc
                            ON mc.category_id = sc.category_id
                        LEFT JOIN (
                            SELECT equipment_id, COUNT(*) AS available_qty
                            FROM public.stock_details
                            GROUP BY equipment_id
                        ) stock
                            ON stock.equipment_id = NULLIF(jed.equipment_detail_id, '')::int
                        WHERE jed.job_id = %s
                        ORDER BY jed.id
                    """, [edit_id])
                    columns = [col[0] for col in cursor.description]
                    equipment_data = [dict(zip(columns, r)) for r in cursor.fetchall()]

                    cursor.execute("SELECT * FROM public.job_crew_allocation WHERE job_id = %s ORDER BY id", [edit_id])
                    columns = [col[0] for col in cursor.description]
                    crew_data = [dict(zip(columns, r)) for r in cursor.fetchall()]

                    cursor.execute("SELECT * FROM public.job_sub_vendor WHERE job_id = %s ORDER BY id", [edit_id])
                    columns = [col[0] for col in cursor.description]
                    sub_vendor_data = [dict(zip(columns, r)) for r in cursor.fetchall()]

                    cursor.execute("SELECT * FROM public.job_transportation_allocation WHERE job_id = %s ORDER BY id", [edit_id])
                    columns = [col[0] for col in cursor.description]
                    transport_data = [dict(zip(columns, r)) for r in cursor.fetchall()]

                    cursor.execute("""
                        SELECT *
                        FROM public.job_split_master
                        WHERE parent_job_id = %s
                        ORDER BY split_id
                    """, [edit_id])
                    columns = [col[0] for col in cursor.description]
                    split_data = [dict(zip(columns, r)) for r in cursor.fetchall()]

                elif edit_type == "temp":
                    cursor.execute("SELECT * FROM public.temp WHERE id = %s", [edit_id])
                    columns = [col[0] for col in cursor.description]
                    row = cursor.fetchone()
                    job_data = dict(zip(columns, row)) if row else {}

                    cursor.execute("SELECT * FROM public.temp_equipment_details WHERE temp_id = %s ORDER BY id", [edit_id])
                    columns = [col[0] for col in cursor.description]
                    equipment_data = [dict(zip(columns, r)) for r in cursor.fetchall()]

                    cursor.execute("SELECT * FROM public.temp_crew_allocation WHERE temp_id = %s ORDER BY id", [edit_id])
                    columns = [col[0] for col in cursor.description]
                    crew_data = [dict(zip(columns, r)) for r in cursor.fetchall()]

                    cursor.execute("SELECT * FROM public.temp_sub_vendor WHERE temp_id = %s ORDER BY id", [edit_id])
                    columns = [col[0] for col in cursor.description]
                    sub_vendor_data = [dict(zip(columns, r)) for r in cursor.fetchall()]

                    cursor.execute("SELECT * FROM public.temp_transportation_allocation WHERE temp_id = %s ORDER BY id", [edit_id])
                    columns = [col[0] for col in cursor.description]
                    transport_data = [dict(zip(columns, r)) for r in cursor.fetchall()]

        except Exception as e:
            print("EDIT LOAD ERROR:", str(e))

    return render(request, "inventory/add_job.html", {
        "username": username,
        "edit_id": edit_id,
        "edit_type": edit_type,
        "edit_mode": bool(edit_id),
        "job_data": json.dumps(job_data, default=str),
        "equipment_data": json.dumps(equipment_data, default=str),
        "crew_data": json.dumps(crew_data, default=str),
        "sub_vendor_data": json.dumps(sub_vendor_data, default=str),
        "transport_data": json.dumps(transport_data, default=str),
        "split_data": json.dumps(split_data, default=str),
    })


def get_equipment_meta(request, equipment_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    mc.category_name,
                    sc.name AS sub_category_name,
                    el.equipment_name
                FROM public.equipment_list el
                LEFT JOIN public.sub_category sc
                    ON sc.id = el.sub_category_id
                LEFT JOIN public.master_category mc
                    ON mc.category_id = sc.category_id
                WHERE el.id = %s
            """, [equipment_id])

            row = cursor.fetchone()

        if not row:
            return JsonResponse({
                "category_name": "",
                "sub_category_name": "",
                "equipment_name": ""
            })

        return JsonResponse({
            "category_name": row[0] or "",
            "sub_category_name": row[1] or "",
            "equipment_name": row[2] or ""
        })

    except Exception as e:
        return JsonResponse({
            "error": str(e)
        }, status=500)

def generate_quotation_no():
    with connection.cursor() as cursor:
        cursor.execute("SELECT public.generate_quotation_no()")
        return cursor.fetchone()[0]



def save_temp_child_rows(
    cursor,
    temp_id,
    equipment_location,
    equipment_incharge,
    equipment_event_date,
    equipment_rehearsal_date,
    equipment_categories,
    equipment_ids,
    equipment_qtys,
    rental_prices,
    equipment_totals,
    equipment_notes,
    crew_types,
    crew_days,
    perday_charges,
    crew_totals,
    crew_notes,
    vendor_names,
    sub_equipment_names,
    sub_quantities,
    sub_unit_prices,
    sub_totals,
    transport_amounts,
    driver_names,
    contact_numbers,
    vehicle_numbers,
    outside_driver_names,
    outside_contact_numbers,
    outside_vehicle_numbers
):
    for index, equipment_id in enumerate(equipment_ids):
        if not equipment_id:
            continue

        category_name = ''
        sub_category_name = ''
        equipment_name = equipment_id

        if str(equipment_id).isdigit():

            cursor.execute("""
                SELECT equipment_name
                FROM public.equipment_list
                WHERE id = %s
            """, [equipment_id])

            equipment_row = cursor.fetchone()

            if equipment_row:
                equipment_name = equipment_row[0]

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

    for index, crew_type in enumerate(crew_types):
        if not crew_type:
            continue

        cursor.execute("""
            INSERT INTO public.temp_crew_allocation (
                temp_id,
                crew_type,
                crew_no_of_days,
                perday_charges,
                total,
                crew_notes
            )
            VALUES (%s, %s, %s, %s, %s, %s)
        """, [
            temp_id,
            crew_type,
            crew_days[index] if index < len(crew_days) else '',
            perday_charges[index] if index < len(perday_charges) else '',
            crew_totals[index] if index < len(crew_totals) else '',
            crew_notes[index] if index < len(crew_notes) else ''
        ])

    for index, vendor_name in enumerate(vendor_names):
        if not vendor_name:
            continue

        cursor.execute("""
            INSERT INTO public.temp_sub_vendor (
                temp_id,
                vendor_name,
                sub_equipment_name,
                sub_quantity,
                sub_unit_price,
                sub_total
            )
            VALUES (%s, %s, %s, %s, %s, %s)
        """, [
            temp_id,
            vendor_name,
            sub_equipment_names[index] if index < len(sub_equipment_names) else '',
            sub_quantities[index] if index < len(sub_quantities) else '',
            sub_unit_prices[index] if index < len(sub_unit_prices) else '',
            sub_totals[index] if index < len(sub_totals) else ''
        ])

    total_rows = max(
        len(driver_names),
        len(outside_driver_names),
        len(vehicle_numbers),
        len(outside_vehicle_numbers)
    )

    for index in range(total_rows):
        cursor.execute("""
            INSERT INTO public.temp_transportation_allocation (
                temp_id,
                driver_name,
                contact_number,
                vehicle_number,
                outside_driver_name,
                outside_contact_number,
                outside_vehicle_number,
                transport_amount
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, [
            temp_id,
            driver_names[index] if index < len(driver_names) else '',
            contact_numbers[index] if index < len(contact_numbers) else '',
            vehicle_numbers[index] if index < len(vehicle_numbers) else '',
            outside_driver_names[index] if index < len(outside_driver_names) else '',
            outside_contact_numbers[index] if index < len(outside_contact_numbers) else '',
            outside_vehicle_numbers[index] if index < len(outside_vehicle_numbers) else '',
            transport_amounts[index] if index < len(transport_amounts) else ''
        ])


def save_job_child_rows(
    cursor,
    job_id,
    equipment_location,
    equipment_incharge,
    equipment_event_date,
    equipment_rehearsal_date,
    equipment_categories,
    equipment_ids,
    equipment_qtys,
    rental_prices,
    equipment_totals,
    equipment_notes,
    crew_types,
    crew_days,
    perday_charges,
    crew_totals,
    crew_notes,
    vendor_names,
    sub_equipment_names,
    sub_quantities,
    sub_unit_prices,
    sub_totals,
    transport_amounts,
    driver_names,
    contact_numbers,
    vehicle_numbers,
    outside_driver_names,
    outside_contact_numbers,
    outside_vehicle_numbers,
    selected_split_id=None
):
    # =====================================================
    # EQUIPMENT DETAILS
    # =====================================================
    for index, equipment_id in enumerate(equipment_ids):
        if not equipment_id:
            continue

        equipment_name = equipment_id
        category_name = ''
        sub_category_name = ''

        if str(equipment_id).isdigit():

            cursor.execute("""
                SELECT equipment_name
                FROM public.equipment_list
                WHERE id = %s
            """, [equipment_id])

            equipment_row = cursor.fetchone()

            if equipment_row:
                equipment_name = equipment_row[0]

        cursor.execute("""
            INSERT INTO public.job_equipment_details (
                job_id,
                split_id,
                equipment_detail_id,
                location,
                incharge,
                equipment_setup_date,
                equipment_rehearsal_date,
                equipment_name,
                quantity,
                equipment_unit_price,
                equipment_total,
                equipment_notes,
                category_name,
                sub_category_name
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, [
            job_id,
            selected_split_id,
            equipment_id,
            equipment_location,
            equipment_incharge,
            equipment_event_date,
            equipment_rehearsal_date,
            equipment_name,
            equipment_qtys[index] if index < len(equipment_qtys) else '',
            rental_prices[index] if index < len(rental_prices) else '',
            equipment_totals[index] if index < len(equipment_totals) else '',
            equipment_notes[index] if index < len(equipment_notes) else '',
            category_name,
            sub_category_name
        ])

    # =====================================================
    # CREW ALLOCATION
    # keep crew at main job level only
    # =====================================================
    if not selected_split_id:
        for index, crew_type in enumerate(crew_types):
            if not crew_type:
                continue

            cursor.execute("""
                INSERT INTO public.job_crew_allocation (
                    job_id,
                    crew_type,
                    crew_no_of_days,
                    perday_charges,
                    total,
                    crew_notes
                )
                VALUES (%s, %s, %s, %s, %s, %s)
            """, [
                job_id,
                crew_type,
                crew_days[index] if index < len(crew_days) else '',
                perday_charges[index] if index < len(perday_charges) else '',
                crew_totals[index] if index < len(crew_totals) else '',
                crew_notes[index] if index < len(crew_notes) else ''
            ])

        # =====================================================
        # SUB VENDOR
        # =====================================================
        for index, vendor_name in enumerate(vendor_names):
            if not vendor_name:
                continue

            cursor.execute("""
                INSERT INTO public.job_sub_vendor (
                    job_id,
                    vendor_name,
                    sub_equipment_name,
                    sub_quantity
                )
                VALUES (%s, %s, %s, %s)
            """, [
                job_id,
                vendor_name,
                sub_equipment_names[index] if index < len(sub_equipment_names) else '',
                sub_quantities[index] if index < len(sub_quantities) else ''
            ])

        # =====================================================
        # TRANSPORTATION
        # =====================================================
        total_rows = max(
            len(driver_names),
            len(outside_driver_names),
            len(vehicle_numbers),
            len(outside_vehicle_numbers)
        )

        for index in range(total_rows):
            cursor.execute("""
                INSERT INTO public.job_transportation_allocation (
                    job_id,
                    driver_name,
                    contact_number,
                    vehicle_number,
                    outside_driver_name,
                    outside_contact_number,
                    outside_vehicle_number
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, [
                job_id,
                driver_names[index] if index < len(driver_names) else '',
                contact_numbers[index] if index < len(contact_numbers) else '',
                vehicle_numbers[index] if index < len(vehicle_numbers) else '',
                outside_driver_names[index] if index < len(outside_driver_names) else '',
                outside_contact_numbers[index] if index < len(outside_contact_numbers) else '',
                outside_vehicle_numbers[index] if index < len(outside_vehicle_numbers) else ''
            ])
def split_jobs_list(request, job_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    id,
                    job_order_no,
                    job_section,
                    status
                FROM public.jobs
                WHERE parent_job_id = %s
                ORDER BY split_no ASC
            """, [job_id])

            rows = cursor.fetchall()

        data = [
            {
                "id": row[0],
                "job_order_no": row[1],
                "job_section": row[2],
                "status": row[3]
            }
            for row in rows
        ]

        return JsonResponse({"data": data})

    except Exception as e:
        return JsonResponse({"data": [], "error": str(e)}, status=500)


@require_POST
def create_split_job(request, job_id):
    section_name = request.POST.get("job_section", "").strip()

    if not section_name:
        return JsonResponse({
            "success": False,
            "error": "Section / ballroom name is required."
        }, status=400)

    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT public.generate_co_job_no(%s)",
                    [job_id]
                )
                split_job_no = cursor.fetchone()[0]

                cursor.execute("""
                    INSERT INTO public.job_split_master
                    (
                        parent_job_id,
                        split_job_no,
                        section_name,
                        status,
                        created_by,
                        created_date
                    )
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    RETURNING split_id
                """, [
                    job_id,
                    split_job_no,
                    section_name,
                    "Active",
                    request.session.get("user_id")
                ])

                split_id = cursor.fetchone()[0]

        return JsonResponse({
            "success": True,
            "message": "Co job created successfully.",
            "split_id": split_id,
            "split_job_no": split_job_no,
            "section_name": section_name
        })

    except Exception as e:
        return JsonResponse({
            "success": False,
            "error": str(e)
        }, status=500)

def get_split_jobs(request, parent_job_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    split_id,
                    split_job_no,
                    section_name,
                    status
                FROM public.job_split_master
                WHERE parent_job_id = %s
                ORDER BY split_id ASC
            """, [parent_job_id])

            rows = cursor.fetchall()

        return JsonResponse({
            "split_jobs": [
                {
                    "split_id": row[0],
                    "split_job_no": row[1],
                    "section_name": row[2],
                    "status": row[3],
                }
                for row in rows
            ]
        })

    except Exception as e:
        return JsonResponse({
            "split_jobs": [],
            "error": str(e)
        }, status=500)

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
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    el.id,
                    el.equipment_name,
                    COALESCE(mc.category_name, '') AS category_name,
                    COALESCE(sc.name, '') AS sub_category_name,
                    COALESCE(MAX(sd.rental_price), 0) AS rental_price,
                    COALESCE(COUNT(sd.id), 0) AS available_quantity
                FROM public.equipment_list el
                LEFT JOIN public.sub_category sc 
                    ON sc.id = el.sub_category_id
                LEFT JOIN public.master_category mc 
                    ON mc.category_id = sc.category_id
                LEFT JOIN public.stock_details sd 
                    ON sd.equipment_id = el.id
                WHERE el.status = TRUE
                GROUP BY el.id, el.equipment_name, mc.category_name, sc.name
                ORDER BY el.equipment_name
            """)
            rows = cursor.fetchall()

        return JsonResponse({
            "equipment_names": [
                {
                    "id": row[0],
                    "name": row[1],
                    "category_name": row[2],
                    "sub_category_name": row[3],
                    "rental_price": float(row[4] or 0),
                    "available_quantity": int(row[5] or 0),
                    "qty": int(row[5] or 0),
                }
                for row in rows
            ]
        })

    except Exception as e:
        print("FETCH EQUIPMENT ERROR:", str(e))
        return JsonResponse({"equipment_names": [], "error": str(e)}, status=500)


def fetch_rental_price(request):
    equipment_id = request.GET.get("equipment_id")

    if not equipment_id:
        return JsonResponse({"error": "Equipment ID is required"}, status=400)

    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    mc.category_name,
                    sc.name AS sub_category_name,
                    COALESCE(MAX(sd.rental_price), 0) AS rental_price,
                    COALESCE(COUNT(sd.id), 0) AS available_quantity
                FROM public.equipment_list el
                JOIN public.sub_category sc
                    ON el.sub_category_id = sc.id
                JOIN public.master_category mc
                    ON sc.category_id = mc.category_id
                LEFT JOIN public.stock_details sd
                    ON sd.equipment_id = el.id
                WHERE el.id = %s
                  AND el.status = TRUE
                GROUP BY mc.category_name, sc.name
                LIMIT 1
            """, [equipment_id])

            row = cursor.fetchone()

        if not row:
            return JsonResponse({"error": "Equipment not found"}, status=404)

        return JsonResponse({
            "category_name": row[0],
            "sub_category_name": row[1],
            "rental_price": float(row[2] or 0),
            "available_quantity": int(row[3] or 0)
        })

    except Exception as e:
        print("FETCH RENTAL PRICE ERROR:", str(e))
        return JsonResponse({"error": str(e)}, status=500)


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
                SELECT *
                FROM (
                    SELECT
                        t.id::int AS id,
                        COALESCE(t.job_reference_no, t.quotation_no, '')::varchar AS job_reference_no,
                        COALESCE(t.quotation_no, t.job_reference_no, '')::varchar AS quotation_no,
                        ''::varchar AS main_job_no,
                        ''::varchar AS job_order_no,
                        COALESCE(t.title, '')::varchar AS title,
                        COALESCE(t.client_name, '')::varchar AS client_name,
                        COALESCE(t.venue_name, '')::varchar AS venue_name,
                        COALESCE(t.status, 'Quotation')::varchar AS status,
                        t.created_date AS created_date,
                        'temp'::varchar AS source_type
                    FROM public.temp t
                    WHERE COALESCE(t.is_active, TRUE) = TRUE
                      AND COALESCE(t.status, '') = 'Quotation'

                    UNION ALL

                    SELECT
                        j.id::int AS id,
                        COALESCE(j.job_order_no, j.main_job_no, j.job_reference_no, '')::varchar AS job_reference_no,
                        COALESCE(j.quotation_no, '')::varchar AS quotation_no,
                        COALESCE(j.main_job_no, '')::varchar AS main_job_no,
                        COALESCE(j.job_order_no, '')::varchar AS job_order_no,
                        COALESCE(j.title, '')::varchar AS title,
                        COALESCE(j.client_name, '')::varchar AS client_name,
                        COALESCE(j.venue_name, '')::varchar AS venue_name,
                        COALESCE(j.status, '')::varchar AS status,
                        j.created_date AS created_date,
                        'job'::varchar AS source_type
                    FROM public.jobs j
                    WHERE COALESCE(j.is_active, TRUE) = TRUE
                      AND j.parent_job_id IS NULL
                ) x
                ORDER BY x.created_date DESC NULLS LAST, x.id DESC
            """)

            rows = cursor.fetchall()

        data = []
        for row in rows:
            data.append({
                "id": row[0],
                "job_reference_no": row[1],
                "quotation_no": row[2],
                "main_job_no": row[3],
                "job_order_no": row[4],
                "title": row[5],
                "client_name": row[6],
                "venue_name": row[7],
                "status": row[8],
                "created_date": row[9].strftime("%Y-%m-%d") if row[9] else "",
                "source_type": row[10],
            })

        return JsonResponse(data, safe=False)

    except Exception as e:
        print("JOBS_LIST ERROR:", str(e))
        return JsonResponse({
            "success": False,
            "error": str(e)
        }, status=500)


@require_POST
def inactive_job(request):
    record_id = request.POST.get("id")
    source_type = request.POST.get("source_type")

    if not record_id or source_type not in ["temp", "job"]:
        return JsonResponse({"success": False, "error": "Invalid delete request"}, status=400)

    try:
        with connection.cursor() as cursor:
            if source_type == "temp":
                cursor.execute("""
                    UPDATE public.temp
                    SET is_active = FALSE
                    WHERE id = %s
                """, [record_id])
            else:
                cursor.execute("""
                    UPDATE public.jobs
                    SET is_active = FALSE
                    WHERE id = %s
                """, [record_id])

        return JsonResponse({"success": True, "message": "Record inactive successfully."})

    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)

def get_status_counts(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    (SELECT COUNT(*) FROM public.temp 
                     WHERE status = 'Quotation' 
                     AND COALESCE(is_active, TRUE) = TRUE) AS quotation_count,

                    (SELECT COUNT(*) FROM public.jobs 
                     WHERE status = 'Proforma' 
                     AND COALESCE(is_active, TRUE) = TRUE
                     AND parent_job_id IS NULL) AS proforma_count,

                    (SELECT COUNT(*) FROM public.jobs 
                     WHERE status = 'Prepsheet' 
                     AND COALESCE(is_active, TRUE) = TRUE
                     AND parent_job_id IS NULL) AS prepsheet_count,

                    (SELECT COUNT(*) FROM public.jobs 
                     WHERE status = 'Delivery Challan' 
                     AND COALESCE(is_active, TRUE) = TRUE
                     AND parent_job_id IS NULL) AS deliveryChallan_count,

                    (SELECT COUNT(*) FROM public.jobs 
                     WHERE COALESCE(is_active, TRUE) = TRUE
                     AND parent_job_id IS NULL) AS job_count
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
        print("STATUS COUNT ERROR:", str(e))
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

def update_crew(request, crew_id):
    if request.method == "POST":
        try:
            crew_designation = request.POST.get("crew_designation", "").strip()
            status = request.POST.get("status", "true").lower() == "true"
            updated_by = request.session.get("user_id")

            if not crew_designation:
                return JsonResponse({
                    "success": False,
                    "message": "Crew designation is required."
                })

            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT *
                    FROM public.manage_crew(%s, %s, %s, %s, %s)
                """, ['update', crew_id, crew_designation, status, updated_by])

                row = cursor.fetchone()

            return JsonResponse({
                "success": row[6],
                "message": row[5]
            })

        except Exception as e:
            return JsonResponse({
                "success": False,
                "message": str(e)
            }, status=500)

    return JsonResponse({
        "success": False,
        "message": "Invalid request method"
    }, status=405)


def delete_crew(request, crew_id):
    if request.method == "POST":
        try:
            deleted_by = request.session.get("user_id")

            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT *
                    FROM public.manage_crew(%s, %s, %s, %s, %s)
                """, ['delete', crew_id, None, None, deleted_by])

                row = cursor.fetchone()

            return JsonResponse({
                "success": row[6],
                "message": row[5]
            })

        except Exception as e:
            return JsonResponse({
                "success": False,
                "message": str(e)
            }, status=500)

    return JsonResponse({
        "success": False,
        "message": "Invalid request method"
    }, status=405)


def scanning_page(request):
    return render(request, "inventory/scanning_page.html")


def delivery_challan_jobs_api(request):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT 
                t.id,
                t.job_reference_no,
                COALESCE(t.title, '-') AS title,
                COALESCE(t.client_name, '-') AS client_name,
                COALESCE(SUM(CAST(ted.quantity AS INTEGER)), 0) AS total_qty,
                COALESCE(COUNT(td.trans_id), 0) AS scanned_qty
            FROM temp t
            LEFT JOIN temp_equipment_details ted 
                ON ted.temp_id = t.id
            LEFT JOIN transaction_details td 
                ON td.job_id = t.id
               AND td.scan_flag_in = FALSE
            WHERE t.status = 'Delivery Challan'
              AND COALESCE(t.scan_flag, FALSE) = FALSE
              AND COALESCE(t.completion_flag, FALSE) = FALSE
            GROUP BY 
                t.id, t.job_reference_no, t.title, t.client_name
            HAVING COALESCE(SUM(CAST(ted.quantity AS INTEGER)), 0) > COALESCE(COUNT(td.trans_id), 0)
            ORDER BY t.id DESC
        """)
        rows = cursor.fetchall()

    data = []
    for r in rows:
        data.append({
            "job_id": r[0],
            "job_no": r[1],
            "title": r[2],
            "client_name": r[3],
            "total_qty": r[4],
            "scanned_qty": r[5],
            "pending_qty": int(r[4]) - int(r[5]),
        })

    return JsonResponse(data, safe=False)


def delivery_challan_equipment_api(request, job_id):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT 
                ted.equipment_detail_id,
                ted.equipment_name,
                SUM(CAST(ted.quantity AS INTEGER)) AS required_qty,
                COALESCE(sc.scanned_qty, 0) AS scanned_qty
            FROM temp_equipment_details ted
            LEFT JOIN (
                SELECT 
                    equip_details_id,
                    COUNT(*) AS scanned_qty
                FROM transaction_details
                WHERE job_id = %s
                  AND scan_flag_in = FALSE
                GROUP BY equip_details_id
            ) sc ON sc.equip_details_id = ted.equipment_detail_id::TEXT
            WHERE ted.temp_id = %s
            GROUP BY 
                ted.equipment_detail_id,
                ted.equipment_name,
                sc.scanned_qty
            ORDER BY ted.equipment_name
        """, [job_id, job_id])
        rows = cursor.fetchall()

    data = []
    for r in rows:
        required_qty = int(r[2] or 0)
        scanned_qty = int(r[3] or 0)

        data.append({
            "equipment_detail_id": r[0],
            "equipment_name": r[1],
            "required_qty": required_qty,
            "scanned_qty": scanned_qty,
            "pending_qty": required_qty - scanned_qty,
        })

    return JsonResponse(data, safe=False)


@csrf_exempt
def scan_barcode_api(request):
    if request.method != "POST":
        return JsonResponse({"status": 0, "message": "Invalid request method"})

    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"status": 0, "message": "Invalid JSON request"})

    scan_type = data.get("scan_type")
    job_id = data.get("job_id")
    job_no = str(data.get("job_no", "")).strip()
    barcode = str(data.get("barcode", "")).strip()
    user_id = request.session.get("user_id", 1)

    if not barcode:
        return JsonResponse({"status": 0, "message": "Barcode required"})

    try:
        with transaction.atomic():
            with connection.cursor() as cursor:

                cursor.execute("""
                    SELECT 
                        sd.id,
                        sd.equipment_id,
                        sd.barcode_no,
                        COALESCE(sd.scan_flag, FALSE) AS scan_flag,
                        el.equipment_name
                    FROM stock_details sd
                    JOIN equipment_list el ON el.id = sd.equipment_id
                    WHERE sd.barcode_no = %s
                    LIMIT 1
                """, [barcode])
                stock = cursor.fetchone()

                if not stock:
                    return JsonResponse({
                        "status": 0,
                        "message": "Invalid barcode. Barcode not found in stock."
                    })

                stock_id, equipment_id, barcode_no, stock_scan_flag, equipment_name = stock

                if scan_type == "scan_out":
                    return scan_out_delivery_challan(
                        cursor=cursor,
                        job_id=job_id,
                        barcode=barcode,
                        equipment_id=equipment_id,
                        equipment_name=equipment_name,
                        stock_scan_flag=stock_scan_flag,
                        user_id=user_id
                    )

                elif scan_type == "scan_in_job":
                    return scan_in_by_job(
                        cursor=cursor,
                        job_no=job_no,
                        barcode=barcode,
                        equipment_name=equipment_name,
                        user_id=user_id
                    )

                elif scan_type == "global_scan_in":
                    return global_scan_in(
                        cursor=cursor,
                        barcode=barcode,
                        equipment_name=equipment_name,
                        user_id=user_id
                    )

                return JsonResponse({"status": 0, "message": "Invalid scan type"})

    except Exception as e:
        return JsonResponse({"status": 0, "message": str(e)})


def scan_out_delivery_challan(cursor, job_id, barcode, equipment_id, equipment_name, stock_scan_flag, user_id):
    if not job_id:
        return JsonResponse({"status": 0, "message": "Select Delivery Challan job"})

    if stock_scan_flag is True:
        return JsonResponse({"status": 0, "message": "This barcode is already scanned OUT"})

    cursor.execute("""
        SELECT id, job_reference_no, title
        FROM temp
        WHERE id = %s
          AND status = 'Delivery Challan'
          AND COALESCE(scan_flag, FALSE) = FALSE
          AND COALESCE(completion_flag, FALSE) = FALSE
        LIMIT 1
    """, [job_id])
    job = cursor.fetchone()

    if not job:
        return JsonResponse({
            "status": 0,
            "message": "Only active Delivery Challan jobs are allowed"
        })

    real_job_id, job_ref_no, title = job

    cursor.execute("""
        SELECT 
            equipment_detail_id,
            equipment_name,
            SUM(CAST(quantity AS INTEGER)) AS required_qty
        FROM temp_equipment_details
        WHERE temp_id = %s
          AND (
                equipment_detail_id::TEXT = %s
                OR UPPER(equipment_name) = UPPER(%s)
              )
        GROUP BY equipment_detail_id, equipment_name
        LIMIT 1
    """, [real_job_id, str(equipment_id), equipment_name])
    dc_equipment = cursor.fetchone()

    if not dc_equipment:
        return JsonResponse({
            "status": 0,
            "message": f"{equipment_name} is not available in this Delivery Challan"
        })

    equipment_detail_id, dc_equipment_name, required_qty = dc_equipment
    required_qty = int(required_qty or 0)

    cursor.execute("""
        SELECT COUNT(*)
        FROM transaction_details
        WHERE job_id = %s
          AND equip_details_id = %s
          AND scan_flag_in = FALSE
    """, [real_job_id, str(equipment_detail_id)])
    already_scanned_qty = int(cursor.fetchone()[0] or 0)

    if already_scanned_qty >= required_qty:
        return JsonResponse({
            "status": 0,
            "message": f"Required quantity already scanned for {dc_equipment_name}"
        })

    cursor.execute("""
        SELECT COUNT(*)
        FROM transaction_details
        WHERE barcode = %s
          AND scan_flag_in = FALSE
    """, [barcode])
    barcode_out_count = int(cursor.fetchone()[0] or 0)

    if barcode_out_count > 0:
        return JsonResponse({
            "status": 0,
            "message": "This barcode is already OUT in another transaction"
        })

    cursor.execute("""
        INSERT INTO transaction_details
        (
            job_id,
            job_ref_no,
            equip_details_id,
            equipment_name,
            barcode,
            scan_flag_in,
            scan_out_date_time,
            scan_out_by,
            venue_out
        )
        VALUES (%s, %s, %s, %s, %s, FALSE, NOW(), %s, FALSE)
    """, [
        real_job_id,
        job_ref_no,
        str(equipment_detail_id),
        dc_equipment_name,
        barcode,
        user_id
    ])

    cursor.execute("""
        UPDATE stock_details
        SET scan_flag = TRUE
        WHERE barcode_no = %s
    """, [barcode])

    new_scanned_qty = already_scanned_qty + 1
    pending_qty = required_qty - new_scanned_qty

    cursor.execute("""
        SELECT COALESCE(SUM(CAST(quantity AS INTEGER)), 0)
        FROM temp_equipment_details
        WHERE temp_id = %s
    """, [real_job_id])
    total_required = int(cursor.fetchone()[0] or 0)

    cursor.execute("""
        SELECT COUNT(*)
        FROM transaction_details
        WHERE job_id = %s
          AND scan_flag_in = FALSE
    """, [real_job_id])
    total_scanned = int(cursor.fetchone()[0] or 0)

    if total_required > 0 and total_scanned >= total_required:
        cursor.execute("""
            UPDATE temp
            SET scan_flag = TRUE
            WHERE id = %s
        """, [real_job_id])

    return JsonResponse({
        "status": 1,
        "message": "Scan OUT successful",
        "job_no": job_ref_no,
        "barcode": barcode,
        "equipment_name": dc_equipment_name,
        "required_qty": required_qty,
        "scanned_qty": new_scanned_qty,
        "pending_qty": pending_qty,
        "total_required": total_required,
        "total_scanned": total_scanned
    })


def scan_in_by_job(cursor, job_no, barcode, equipment_name, user_id):
    if not job_no:
        return JsonResponse({"status": 0, "message": "Enter Job No"})

    cursor.execute("""
        SELECT trans_id, job_id, job_ref_no
        FROM transaction_details
        WHERE barcode = %s
          AND job_ref_no = %s
          AND scan_flag_in = FALSE
        ORDER BY scan_out_date_time DESC
        LIMIT 1
    """, [barcode, job_no])
    txn = cursor.fetchone()

    if not txn:
        return JsonResponse({
            "status": 0,
            "message": "No pending OUT transaction found for this Job No and barcode"
        })

    trans_id, job_id, job_ref_no = txn

    cursor.execute("""
        UPDATE transaction_details
        SET scan_flag_in = TRUE,
            scan_in_date_time = NOW(),
            scan_in_by = %s
        WHERE trans_id = %s
    """, [user_id, trans_id])

    cursor.execute("""
        UPDATE stock_details
        SET scan_flag = FALSE
        WHERE barcode_no = %s
    """, [barcode])

    return JsonResponse({
        "status": 1,
        "message": "Scan IN successful",
        "job_no": job_ref_no,
        "barcode": barcode,
        "equipment_name": equipment_name
    })


def global_scan_in(cursor, barcode, equipment_name, user_id):
    cursor.execute("""
        SELECT trans_id, job_id, job_ref_no
        FROM transaction_details
        WHERE barcode = %s
          AND scan_flag_in = FALSE
        ORDER BY scan_out_date_time DESC
        LIMIT 1
    """, [barcode])
    txn = cursor.fetchone()

    if not txn:
        return JsonResponse({
            "status": 0,
            "message": "No pending OUT record found for this barcode"
        })

    trans_id, job_id, job_ref_no = txn

    cursor.execute("""
        UPDATE transaction_details
        SET scan_flag_in = TRUE,
            scan_in_date_time = NOW(),
            scan_in_by = %s
        WHERE trans_id = %s
    """, [user_id, trans_id])

    cursor.execute("""
        UPDATE stock_details
        SET scan_flag = FALSE
        WHERE barcode_no = %s
    """, [barcode])

    return JsonResponse({
        "status": 1,
        "message": "Global Scan IN successful",
        "job_no": job_ref_no,
        "barcode": barcode,
        "equipment_name": equipment_name
    })

def dispatch_loading(request):
    return render(request, "inventory/dispatch_loading.html")


def dispatch_jobs_api(request):
    try:
        search = request.GET.get("search", "").strip()
        page = int(request.GET.get("page", 1) or 1)
        page_size = int(request.GET.get("page_size", 10) or 10)

        if page < 1:
            page = 1

        if page_size < 1:
            page_size = 10

        offset = (page - 1) * page_size

        where_clause = ""
        params = []

        if search:
            where_clause = """
                AND (
                    COALESCE(job_order_no, '') ILIKE %s
                    OR COALESCE(main_job_no, '') ILIKE %s
                    OR COALESCE(job_reference_no, '') ILIKE %s
                    OR COALESCE(title, '') ILIKE %s
                    OR COALESCE(client_name, '') ILIKE %s
                )
            """
            params.extend([f"%{search}%"] * 5)

        with connection.cursor() as cursor:
            cursor.execute(f"""
                SELECT COUNT(*)
                FROM public.jobs
                WHERE status = 'Delivery Challan'
                  AND COALESCE(loading_status, 'Pending') <> 'Fully Loaded'
                {where_clause}
            """, params)

            total_records = cursor.fetchone()[0]

            query_params = params + [page_size, offset]

            cursor.execute(f"""
                SELECT
                    id,
                    COALESCE(job_order_no, main_job_no, job_reference_no) AS job_no,
                    COALESCE(title, '-') AS title,
                    COALESCE(client_name, '-') AS client_name
                FROM public.jobs
                WHERE status = 'Delivery Challan'
                  AND COALESCE(loading_status, 'Pending') <> 'Fully Loaded'
                {where_clause}
                ORDER BY id DESC
                LIMIT %s OFFSET %s
            """, query_params)

            rows = cursor.fetchall()

        total_pages = (total_records + page_size - 1) // page_size

        return JsonResponse({
            "status": 1,
            "total_records": total_records,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "data": [
                {
                    "job_id": row[0],
                    "job_no": row[1],
                    "title": row[2],
                    "client_name": row[3],
                }
                for row in rows
            ]
        })

    except Exception as e:
        return JsonResponse({
            "status": 0,
            "message": str(e),
            "data": [],
            "total_records": 0,
            "page": 1,
            "page_size": 10,
            "total_pages": 0
        }, status=500)


def update_job_loading_status(cursor, job_id):
    cursor.execute("""
        SELECT COALESCE(SUM(quantity::numeric), 0)
        FROM public.job_equipment_details
        WHERE job_id = %s
    """, [job_id])

    required_qty = int(float(cursor.fetchone()[0] or 0))

    cursor.execute("""
        SELECT COUNT(*)
        FROM public.transaction_details
        WHERE job_id = %s
          AND COALESCE(scan_flag_in, false) = false
    """, [job_id])

    scanned_qty = int(cursor.fetchone()[0] or 0)

    if scanned_qty <= 0:
        loading_status = "Pending"
    elif scanned_qty < required_qty:
        loading_status = "Partially Loaded"
    else:
        loading_status = "Fully Loaded"

    cursor.execute("""
        UPDATE public.jobs
        SET loading_status = %s
        WHERE id = %s
    """, [loading_status, job_id])

def job_sections(request, job_id):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT split_id, split_job_no, section_name
            FROM public.job_split_master
            WHERE parent_job_id = %s
            ORDER BY split_id
        """, [job_id])
        rows = cursor.fetchall()

    return JsonResponse([
        {
            "split_id": r[0],
            "split_job_no": r[1],
            "section_name": r[2],
        }
        for r in rows
    ], safe=False)


def dispatch_job_equipment(request, job_id):
    split_id = request.GET.get("split_id", "").strip()

    with connection.cursor() as cursor:
        if split_id:
            cursor.execute("""
                SELECT
                    jed.equipment_detail_id,
                    COALESCE(el.equipment_name, jed.equipment_name) AS equipment_name,
                    SUM(COALESCE(jed.quantity::numeric, 0)) AS required_qty,
                    COALESCE(sc.scanned_qty, 0) AS scanned_qty
                FROM public.job_equipment_details jed
                LEFT JOIN public.equipment_list el
                    ON el.id::text = jed.equipment_detail_id::text
                LEFT JOIN (
                    SELECT equipment_name, COUNT(*) AS scanned_qty
                    FROM public.transaction_details
                    WHERE job_id = %s
                      AND split_id = %s
                      AND COALESCE(scan_flag_in, false) = false
                    GROUP BY equipment_name
                ) sc ON UPPER(sc.equipment_name) = UPPER(COALESCE(el.equipment_name, jed.equipment_name))
                WHERE jed.job_id = %s
                  AND jed.split_id = %s
                GROUP BY jed.equipment_detail_id, el.equipment_name, jed.equipment_name, sc.scanned_qty
                ORDER BY equipment_name
            """, [job_id, split_id, job_id, split_id])
        else:
            cursor.execute("""
                SELECT
                    jed.equipment_detail_id,
                    COALESCE(el.equipment_name, jed.equipment_name) AS equipment_name,
                    SUM(COALESCE(jed.quantity::numeric, 0)) AS required_qty,
                    COALESCE(sc.scanned_qty, 0) AS scanned_qty
                FROM public.job_equipment_details jed
                LEFT JOIN public.equipment_list el
                    ON el.id::text = jed.equipment_detail_id::text
                LEFT JOIN (
                    SELECT equipment_name, COUNT(*) AS scanned_qty
                    FROM public.transaction_details
                    WHERE job_id = %s
                      AND split_id IS NULL
                      AND COALESCE(scan_flag_in, false) = false
                    GROUP BY equipment_name
                ) sc ON UPPER(sc.equipment_name) = UPPER(COALESCE(el.equipment_name, jed.equipment_name))
                WHERE jed.job_id = %s
                  AND jed.split_id IS NULL
                GROUP BY jed.equipment_detail_id, el.equipment_name, jed.equipment_name, sc.scanned_qty
                ORDER BY equipment_name
            """, [job_id, job_id])

        rows = cursor.fetchall()

    data = []
    for r in rows:
        required_qty = int(float(r[2] or 0))
        scanned_qty = int(r[3] or 0)

        data.append({
            "equipment_id": r[0],
            "equipment_name": r[1],
            "required_qty": required_qty,
            "scanned_qty": scanned_qty,
            "pending_qty": required_qty - scanned_qty,
        })

    return JsonResponse(data, safe=False)


def dispatch_scanned_list_api(request, job_id):
    split_id = request.GET.get("split_id", "").strip()

    with connection.cursor() as cursor:
        if split_id:
            cursor.execute("""
                SELECT barcode, equipment_name, scan_out_date_time
                FROM public.transaction_details
                WHERE job_id = %s
                  AND split_id = %s
                  AND COALESCE(scan_flag_in, false) = false
                ORDER BY scan_out_date_time DESC
            """, [job_id, split_id])
        else:
            cursor.execute("""
                SELECT barcode, equipment_name, scan_out_date_time
                FROM public.transaction_details
                WHERE job_id = %s
                  AND split_id IS NULL
                  AND COALESCE(scan_flag_in, false) = false
                ORDER BY scan_out_date_time DESC
            """, [job_id])

        rows = cursor.fetchall()

    return JsonResponse([
        {
            "barcode": r[0],
            "equipment_name": r[1],
            "scan_time": r[2].strftime("%d-%m-%Y %I:%M %p") if r[2] else "-"
        }
        for r in rows
    ], safe=False)


@csrf_exempt
@require_POST
def dispatch_scan_api(request):
    try:
        data = json.loads(request.body.decode("utf-8"))

        job_id = data.get("job_id")
        split_id = data.get("split_id")
        barcode = str(data.get("barcode", "")).strip()
        user_id = request.session.get("user_id", 1)

        split_id = str(split_id).strip() if split_id not in [None, "", "null"] else None

        if not job_id:
            return JsonResponse({"status": 0, "message": "Select Delivery Challan Job"})

        if not barcode:
            return JsonResponse({"status": 0, "message": "Barcode required"})

        with transaction.atomic():
            with connection.cursor() as cursor:

                cursor.execute("""
                    SELECT id, COALESCE(job_order_no, main_job_no, job_reference_no) AS job_no
                    FROM public.jobs
                    WHERE id = %s
                    LIMIT 1
                """, [job_id])
                job = cursor.fetchone()

                if not job:
                    return JsonResponse({"status": 0, "message": "Invalid job selected"})

                real_job_id, job_no = job

                if split_id:
                    cursor.execute("""
                        SELECT
                            sd.id,
                            sd.equipment_id,
                            sd.barcode_no,
                            COALESCE(sd.scan_flag, false) AS scan_flag,
                            COALESCE(el.equipment_name, jed.equipment_name) AS equipment_name,
                            jed.quantity
                        FROM public.stock_details sd
                        JOIN public.job_equipment_details jed
                            ON jed.equipment_detail_id::integer = sd.equipment_id
                        LEFT JOIN public.equipment_list el
                            ON el.id = sd.equipment_id
                        WHERE jed.job_id = %s
                          AND jed.split_id = %s
                          AND sd.barcode_no = %s
                        LIMIT 1
                    """, [job_id, split_id, barcode])
                else:
                    cursor.execute("""
                        SELECT
                            sd.id,
                            sd.equipment_id,
                            sd.barcode_no,
                            COALESCE(sd.scan_flag, false) AS scan_flag,
                            COALESCE(el.equipment_name, jed.equipment_name) AS equipment_name,
                            jed.quantity
                        FROM public.stock_details sd
                        JOIN public.job_equipment_details jed
                            ON jed.equipment_detail_id::integer = sd.equipment_id
                        LEFT JOIN public.equipment_list el
                            ON el.id = sd.equipment_id
                        WHERE jed.job_id = %s
                          AND jed.split_id IS NULL
                          AND sd.barcode_no = %s
                        LIMIT 1
                    """, [job_id, barcode])

                stock = cursor.fetchone()

                if not stock:
                    return JsonResponse({
                        "status": 0,
                        "message": "Barcode not found for selected job / co-job section"
                    })

                stock_id, equipment_id, barcode_no, scan_flag, equipment_name, required_qty = stock

                if scan_flag:
                    return JsonResponse({
                        "status": 0,
                        "message": "This barcode is already OUT"
                    })

                if split_id:
                    cursor.execute("""
                        SELECT COUNT(*)
                        FROM public.transaction_details
                        WHERE job_id = %s
                          AND split_id = %s
                          AND barcode = %s
                          AND COALESCE(scan_flag_in, false) = false
                    """, [job_id, split_id, barcode])
                else:
                    cursor.execute("""
                        SELECT COUNT(*)
                        FROM public.transaction_details
                        WHERE job_id = %s
                          AND split_id IS NULL
                          AND barcode = %s
                          AND COALESCE(scan_flag_in, false) = false
                    """, [job_id, barcode])

                if int(cursor.fetchone()[0] or 0) > 0:
                    return JsonResponse({
                        "status": 0,
                        "message": "This barcode is already scanned for selected job / section"
                    })

                if split_id:
                    cursor.execute("""
                        SELECT
                            COALESCE(SUM(quantity::numeric), 0)
                        FROM public.job_equipment_details
                        WHERE job_id = %s
                          AND split_id = %s
                          AND equipment_detail_id::text = %s
                    """, [job_id, split_id, str(equipment_id)])

                    cursor.execute("""
                        SELECT COUNT(*)
                        FROM public.transaction_details
                        WHERE job_id = %s
                          AND split_id = %s
                          AND equipment_name = %s
                          AND COALESCE(scan_flag_in, false) = false
                    """, [job_id, split_id, equipment_name])
                else:
                    cursor.execute("""
                        SELECT
                            COALESCE(SUM(quantity::numeric), 0)
                        FROM public.job_equipment_details
                        WHERE job_id = %s
                          AND split_id IS NULL
                          AND equipment_detail_id::text = %s
                    """, [job_id, str(equipment_id)])

                    cursor.execute("""
                        SELECT COUNT(*)
                        FROM public.transaction_details
                        WHERE job_id = %s
                          AND split_id IS NULL
                          AND equipment_name = %s
                          AND COALESCE(scan_flag_in, false) = false
                    """, [job_id, equipment_name])

                scanned_qty = int(cursor.fetchone()[0] or 0)

                cursor.execute("""
                    SELECT COALESCE(SUM(quantity::numeric), 0)
                    FROM public.job_equipment_details
                    WHERE job_id = %s
                      AND equipment_detail_id::text = %s
                      AND (
                            (%s IS NULL AND split_id IS NULL)
                            OR
                            (%s IS NOT NULL AND split_id = %s::integer)
                          )
                """, [job_id, str(equipment_id), split_id, split_id, split_id])

                required_qty = int(float(cursor.fetchone()[0] or 0))

                if scanned_qty >= required_qty:
                    return JsonResponse({
                        "status": 0,
                        "message": f"Required quantity already scanned for {equipment_name}"
                    })

                cursor.execute("""
                    INSERT INTO public.transaction_details (
                        job_id,
                        job_ref_no,
                        equipment_name,
                        barcode,
                        scan_out_by,
                        scan_out_date_time,
                        scan_flag_in,
                        split_id
                    )
                    VALUES (%s, %s, %s, %s, %s, NOW(), false, %s)
                """, [
                    real_job_id,
                    job_no,
                    equipment_name,
                    barcode_no,
                    user_id,
                    int(split_id) if split_id else None
                ])

                cursor.execute("""
                    UPDATE public.stock_details
                    SET scan_flag = true,
                        current_status = 'OUT',
                        last_movement_type = 'DISPATCH OUT',
                        last_movement_datetime = NOW()
                    WHERE id = %s
                """, [stock_id])

        return JsonResponse({
            "status": 1,
            "message": "Barcode scanned successfully",
            "equipment_name": equipment_name
        })

    except Exception as e:
        return JsonResponse({
            "status": 0,
            "message": str(e)
        })

def job_return(request):
    return render(request, "inventory/job_return.html", {
        "page_title": "Job Return",
        "page_subtitle": "Scan returned equipment against Job No",
        "api_url": "/api/inventory-movement/"
    })


def quick_return(request):
    return render(request, "inventory/quick_return.html", {
        "page_title": "Quick Return",
        "page_subtitle": "Scan barcode and auto-return latest OUT item",
        "api_url": "/api/inventory-movement/"
    })


def warehouse_transfer(request):
    return render(request, "inventory/warehouse_transfer.html", {
        "page_title": "Warehouse Transfer",
        "page_subtitle": "Transfer equipment between warehouses",
        "api_url": "/api/inventory-movement/"
    })


def maintenance_out(request):
    return render(request, "inventory/maintenance_out.html", {
        "page_title": "Maintenance Out",
        "page_subtitle": "Send equipment for repair or service",
        "api_url": "/api/inventory-movement/"
    })


def maintenance_return(request):
    return render(request, "inventory/maintenance_return.html", {
        "page_title": "Maintenance Return",
        "page_subtitle": "Receive equipment back from maintenance",
        "api_url": "/api/inventory-movement/"
    })


def damage_missing(request):
    return render(request, "inventory/damage_missing.html", {
        "page_title": "Missing / Damage Entry",
        "page_subtitle": "Mark equipment as missing, damaged, or scrap",
        "api_url": "/api/inventory-movement/"
    })

def return_jobs_api(request):
    try:
        search = request.GET.get("search", "").strip()
        page = int(request.GET.get("page", 1) or 1)
        page_size = int(request.GET.get("page_size", 25) or 25)

        if page < 1:
            page = 1

        offset = (page - 1) * page_size

        where = """
            WHERE td.scan_out_date_time IS NOT NULL
              AND COALESCE(td.scan_flag_in, FALSE) = FALSE
        """

        params = []

        if search:
            where += """
                AND (
                    COALESCE(td.job_ref_no, '') ILIKE %s
                    OR COALESCE(j.title, '') ILIKE %s
                    OR COALESCE(j.client_name, '') ILIKE %s
                )
            """
            like = f"%{search}%"
            params.extend([like, like, like])

        with connection.cursor() as cursor:
            cursor.execute(f"""
                SELECT COUNT(*)
                FROM (
                    SELECT td.job_id, td.job_ref_no
                    FROM public.transaction_details td
                    LEFT JOIN public.jobs j
                        ON j.id = td.job_id
                    {where}
                    GROUP BY td.job_id, td.job_ref_no
                ) x
            """, params)

            total_records = cursor.fetchone()[0]

            cursor.execute(f"""
                SELECT
                    td.job_id,
                    td.job_ref_no AS job_no,
                    COALESCE(MAX(j.title), '') AS title,
                    COALESCE(MAX(j.client_name), '') AS client_name,
                    COUNT(td.trans_id) AS pending_return_qty
                FROM public.transaction_details td
                LEFT JOIN public.jobs j
                    ON j.id = td.job_id
                {where}
                GROUP BY td.job_id, td.job_ref_no
                ORDER BY MAX(td.trans_id) DESC
                LIMIT %s OFFSET %s
            """, params + [page_size, offset])

            rows = cursor.fetchall()

        return JsonResponse({
            "status": 1,
            "data": [
                {
                    "job_id": row[0],
                    "job_no": row[1],
                    "title": row[2],
                    "client_name": row[3],
                    "pending_return_qty": row[4],
                }
                for row in rows
            ],
            "page": page,
            "page_size": page_size,
            "total_records": total_records,
            "total_pages": max((total_records + page_size - 1) // page_size, 1),
        })

    except Exception as e:
        print("RETURN JOBS API ERROR:", str(e))
        return JsonResponse({
            "status": 0,
            "message": str(e),
            "data": []
        }, status=500)


def return_scanned_items_api(request, job_no):
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    td.trans_id,
                    td.barcode,
                    td.equipment_name,
                    td.scan_out_date_time,
                    td.scan_in_date_time,
                    COALESCE(td.scan_flag_in, FALSE) AS scan_flag_in
                FROM public.transaction_details td
                WHERE TRIM(COALESCE(td.job_ref_no, '')) = TRIM(%s)
                  AND td.scan_out_date_time IS NOT NULL
                ORDER BY td.trans_id DESC
            """, [job_no])

            rows = cursor.fetchall()

        return JsonResponse({
            "status": 1,
            "data": [
                {
                    "trans_id": row[0],
                    "barcode": row[1],
                    "equipment_name": row[2],
                    "dispatch_time": row[3].strftime("%d-%m-%Y %H:%M") if row[3] else "",
                    "return_time": row[4].strftime("%d-%m-%Y %H:%M") if row[4] else "",
                    "return_status": "Returned" if row[5] else "Pending",
                }
                for row in rows
            ]
        })

    except Exception as e:
        return JsonResponse({
            "status": 0,
            "message": str(e),
            "data": []
        }, status=500)


@csrf_exempt
def scan_job_return_api(request):
    try:
        data = json.loads(request.body.decode("utf-8"))

        job_no = data.get("job_no", "").strip()
        barcode = data.get("barcode", "").strip()
        user_id = request.session.get("user_id")

        if not job_no:
            return JsonResponse({"status": 0, "message": "Job No is required"})

        if not barcode:
            return JsonResponse({"status": 0, "message": "Barcode is required"})

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    trans_id,
                    equipment_name,
                    scan_flag_in
                FROM public.transaction_details
                WHERE TRIM(COALESCE(job_ref_no, '')) = TRIM(%s)
                  AND TRIM(COALESCE(barcode, '')) = TRIM(%s)
                  AND scan_out_date_time IS NOT NULL
                ORDER BY trans_id DESC
                LIMIT 1
            """, [job_no, barcode])

            row = cursor.fetchone()

            if not row:
                return JsonResponse({
                    "status": 0,
                    "message": "This barcode is not dispatched for selected job"
                })

            trans_id = row[0]
            equipment_name = row[1]
            already_returned = row[2]

            if already_returned:
                return JsonResponse({
                    "status": 0,
                    "message": "This barcode is already returned"
                })

            cursor.execute("""
                UPDATE public.transaction_details
                SET
                    scan_flag_in = TRUE,
                    scan_in_date_time = NOW(),
                    scan_in_by = %s
                WHERE trans_id = %s
                RETURNING scan_in_date_time
            """, [user_id, trans_id])

            return_time = cursor.fetchone()[0]

        return JsonResponse({
            "status": 1,
            "message": "Return scanned successfully",
            "barcode": barcode,
            "equipment_name": equipment_name,
            "return_time": return_time.strftime("%d-%m-%Y %H:%M")
        })

    except Exception as e:
        return JsonResponse({
            "status": 0,
            "message": str(e)
        }, status=500)


@csrf_exempt
def quick_return_scan_api(request):
    try:
        data = json.loads(request.body.decode("utf-8"))

        barcode = data.get("barcode", "").strip()
        user_id = request.session.get("user_id")

        if not barcode:
            return JsonResponse({
                "status": 0,
                "message": "Barcode is required"
            })

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    td.trans_id,
                    td.job_id,
                    td.job_ref_no,
                    td.equipment_name,
                    td.barcode,
                    COALESCE(j.client_name, '') AS client_name,
                    COALESCE(td.scan_flag_in, FALSE) AS scan_flag_in
                FROM public.transaction_details td
                LEFT JOIN public.jobs j
                    ON j.id = td.job_id
                WHERE TRIM(COALESCE(td.barcode, '')) = TRIM(%s)
                  AND td.scan_out_date_time IS NOT NULL
                ORDER BY td.scan_out_date_time DESC, td.trans_id DESC
                LIMIT 1
            """, [barcode])

            row = cursor.fetchone()

            if not row:
                return JsonResponse({
                    "status": 0,
                    "message": "No OUT transaction found for this barcode"
                })

            trans_id = row[0]
            job_no = row[2]
            equipment_name = row[3]
            barcode_no = row[4]
            client_name = row[5]
            already_returned = row[6]

            if already_returned:
                return JsonResponse({
                    "status": 0,
                    "message": "This barcode is already returned"
                })

            cursor.execute("""
                UPDATE public.transaction_details
                SET
                    scan_flag_in = TRUE,
                    scan_in_date_time = NOW(),
                    scan_in_by = %s
                WHERE trans_id = %s
                RETURNING scan_in_date_time
            """, [user_id, trans_id])

            return_time = cursor.fetchone()[0]

        return JsonResponse({
            "status": 1,
            "message": "Quick return scanned successfully",
            "barcode": barcode_no,
            "equipment_name": equipment_name,
            "job_no": job_no,
            "client_name": client_name,
            "status_text": "Returned",
            "return_time": return_time.strftime("%d-%m-%Y %H:%M")
        })

    except Exception as e:
        return JsonResponse({
            "status": 0,
            "message": str(e)
        }, status=500)
