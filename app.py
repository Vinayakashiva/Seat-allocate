from flask import Flask, render_template, request, jsonify, redirect, url_for, Response
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
import csv
from io import StringIO
import json
from urllib.parse import unquote
import matplotlib
from datetime import datetime
from twilio.rest import Client
from dotenv import load_dotenv
import os

matplotlib.use('Agg')
import matplotlib.pyplot as plt

load_dotenv()

app = Flask(__name__)
CORS(app)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///office_seats.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Twilio configuration
app.config['TWILIO_ACCOUNT_SID'] = os.getenv('TWILIO_ACCOUNT_SID')
app.config['TWILIO_AUTH_TOKEN'] = os.getenv('TWILIO_AUTH_TOKEN')
app.config['TWILIO_PHONE_NUMBER'] = os.getenv('TWILIO_PHONE_NUMBER')

# Initialize Twilio client
client = Client(app.config['TWILIO_ACCOUNT_SID'], app.config['TWILIO_AUTH_TOKEN'])

db = SQLAlchemy(app)

# Utility constants
WATER_RATE = 2.0
WATER_LITERS_PER_SEAT = 5
POWER_RATE = 5.0
POWER_KWH_PER_SEAT = 2.5


class Office(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    location = db.Column(db.String(200))
    capacity = db.Column(db.Integer, nullable=False)
    seats = db.relationship('Seat', backref='office', lazy=True)


class Seat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    office_id = db.Column(db.Integer, db.ForeignKey('office.id'), nullable=False)
    seat_number = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), default='available')
    occupant = db.Column(db.String(100))
    department = db.Column(db.String(100))
    phone = db.Column(db.String(20))


with app.app_context():
    db.create_all()


def currency_format(value):
    try:
        return f"${float(value):,.2f}" if value is not None else "$0.00"
    except (ValueError, TypeError):
        return "$0.00"


app.jinja_env.filters['currency'] = currency_format


@app.route('/')
def index():
    return redirect(url_for('show_offices'))


@app.route('/offices')
def show_offices():
    return render_template('offices.html')


@app.route('/offices/<int:office_id>')
def show_seats(office_id):
    office = Office.query.get_or_404(office_id)
    return render_template('seats.html', office=office)


@app.route('/allocate', methods=['GET', 'POST'])
def allocate_seats():
    if request.method == 'POST':
        departments = request.form.getlist('department_name')
        counts = request.form.getlist('employee_count')
        phones = request.form.getlist('phone_number')

        dept_data = []
        for dept, count, phone in zip(departments, counts, phones):
            if dept and count.isdigit() and phone:
                dept_data.append({
                    'name': dept.strip(),
                    'count': int(count),
                    'phone': phone.strip()
                })

        if not dept_data:
            return "No valid allocation data submitted", 400

        total_requested = sum(item['count'] for item in dept_data)
        total_available = Seat.query.filter_by(status='available').count()

        if total_available < total_requested:
            return f"Not enough seats available. Required: {total_requested}, Available: {total_available}", 400

        allocation_report = {}
        notification_status = {}
        department_stats = {}
        offices = Office.query.order_by(Office.id).all()

        try:
            for dept in dept_data:
                department = dept['name']
                required = dept['count']
                phone = dept['phone']
                allocated = 0
                allocation_report[department] = {}
                department_stats[department] = {
                    'phone': phone,
                    'seats': 0,
                    'water': 0,
                    'power': 0
                }

                for office in offices:
                    available_seats = Seat.query.filter_by(
                        office_id=office.id,
                        status='available'
                    ).limit(required - allocated).all()

                    num_allocated = len(available_seats)
                    if num_allocated == 0:
                        continue

                    for seat in available_seats:
                        seat.status = 'occupied'
                        seat.department = department
                        seat.phone = phone
                    db.session.commit()

                    allocation_report[department][office.name] = num_allocated
                    allocated += num_allocated
                    department_stats[department]['seats'] += num_allocated

                    if allocated >= required:
                        break

                # Send SMS notification
                try:
                    message = client.messages.create(
                        body=f"{department} allocated {allocated} seats. Offices: {', '.join(allocation_report[department].keys())}",
                        from_=app.config['TWILIO_PHONE_NUMBER'],
                        to=phone
                    )
                    notification_status[department] = 'Sent' if message.sid else 'Failed'
                except Exception as e:
                    notification_status[department] = f'Failed: {str(e)}'

            total_allocated = sum(item['count'] for item in dept_data)

            # Utility calculations
            for dept in department_stats.values():
                dept['water'] = dept['seats'] * WATER_LITERS_PER_SEAT
                dept['power'] = dept['seats'] * POWER_KWH_PER_SEAT

            water_usage = total_allocated * WATER_LITERS_PER_SEAT
            power_usage = total_allocated * POWER_KWH_PER_SEAT
            water_bill = total_allocated * WATER_RATE
            power_bill = total_allocated * POWER_RATE

            # Visualization
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            plt.figure(figsize=(14, 8))
            departments_list = list(department_stats.keys())
            seat_values = [dept['seats'] for dept in department_stats.values()]

            if departments_list:
                max_seats = max(seat_values)
                max_index = seat_values.index(max_seats)

                plt.plot(departments_list, seat_values, marker='o', linestyle='-', color='#2ecc71', linewidth=2)
                plt.scatter(departments_list[max_index], max_seats, color='red', s=200, zorder=5)
                plt.annotate(f'Max: {max_seats} seats\n({departments_list[max_index]})',
                             xy=(departments_list[max_index], max_seats),
                             xytext=(departments_list[max_index], max_seats + 0.1 * max_seats),
                             arrowprops=dict(facecolor='red', shrink=0.05),
                             ha='center')

            plt.title('Department Seat Allocation Analysis', fontsize=16)
            plt.xlabel('Departments', fontsize=12)
            plt.ylabel('Allocated Seats', fontsize=12)
            plt.xticks(rotation=45)
            plt.grid(True, linestyle='--', alpha=0.7)
            plt.tight_layout()

            # Ensure the static directory exists
            static_dir = os.path.join(app.root_path, 'static')
            if not os.path.exists(static_dir):
                os.makedirs(static_dir)

            # Save the chart
            chart_path = os.path.join(static_dir, f"charts_{timestamp}.png")
            plt.savefig(chart_path)
            plt.close()

            return render_template(
                'report.html',
                report=allocation_report,
                total_allocated=total_allocated,
                water_usage=water_usage,
                power_usage=power_usage,
                water_bill=water_bill,
                power_bill=power_bill,
                chart_path=f"charts_{timestamp}.png",  # Relative path for the template
                department_stats=department_stats,
                notification_status=notification_status,
                WATER_RATE=WATER_RATE,
                POWER_RATE=POWER_RATE,
                WATER_LITERS_PER_SEAT=WATER_LITERS_PER_SEAT,
                POWER_KWH_PER_SEAT=POWER_KWH_PER_SEAT
            )
        except Exception as e:
            db.session.rollback()
            return f"Error processing allocation: {str(e)}", 500

    return render_template('allocate.html')


@app.route('/download-report')
def download_report():
    try:
        report_str = unquote(request.args.get('report', '{}'))
        report = json.loads(report_str) if report_str else {}

        total_allocated = int(request.args.get('total_allocated', 0))
        water_usage = float(request.args.get('water_usage', 0))
        power_usage = float(request.args.get('power_usage', 0))
        water_bill = float(request.args.get('water_bill', 0))
        power_bill = float(request.args.get('power_bill', 0))
    except Exception as e:
        return f"Error generating report: {str(e)}", 400

    si = StringIO()
    cw = csv.writer(si)

    cw.writerow([
        'Department', 'Office', 'Seats Allocated',
        'Water (L)', 'Power (kWh)',
        'Water Cost', 'Power Cost'
    ])

    for department, offices in report.items():
        for office, count in offices.items():
            cw.writerow([
                department,
                office,
                count,
                count * WATER_LITERS_PER_SEAT,
                count * POWER_KWH_PER_SEAT,
                count * WATER_RATE,
                count * POWER_RATE
            ])

    cw.writerow([
        'TOTAL', '', total_allocated,
        water_usage, power_usage,
        water_bill, power_bill
    ])

    output = si.getvalue()
    si.close()

    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=allocation_report.csv"}
    )



@app.route('/api/offices', methods=['GET', 'POST'])
def handle_offices():
    if request.method == 'POST':
        data = request.get_json()
        new_office = Office(
            name=data['name'],
            location=data['location'],
            capacity=int(data['capacity'])  # Convert to integer
        )
        db.session.add(new_office)
        db.session.commit()

        # Create seats based on capacity
        for i in range(1, new_office.capacity + 1):  # Use new_office.capacity which is already an integer
            seat = Seat(
                office_id=new_office.id,
                seat_number=f"A{i}",
                status='available'
            )
            db.session.add(seat)
        db.session.commit()
        return jsonify({'id': new_office.id}), 201
    else:
        offices = Office.query.all()
        return jsonify([{
            'id': office.id,
            'name': office.name,
            'location': office.location,
            'capacity': office.capacity
        } for office in offices])

@app.route('/api/offices/<int:office_id>/seats', methods=['GET', 'POST'])
def handle_seats(office_id):
    if request.method == 'POST':
        data = request.get_json()
        existing_seats = Seat.query.filter_by(office_id=office_id).all()
        seat_numbers = [int(s.seat_number[1:]) for s in existing_seats]
        next_number = max(seat_numbers) + 1 if seat_numbers else 1

        for i in range(next_number, next_number + data['count']):
            new_seat = Seat(
                office_id=office_id,
                seat_number=f"A{i}",
                status='available'
            )
            db.session.add(new_seat)
        db.session.commit()
        return jsonify({'message': f'Added {data["count"]} seats'}), 201
    else:
        seats = Seat.query.filter_by(office_id=office_id).all()
        return jsonify([{
            'id': seat.id,
            'seat_number': seat.seat_number,
            'status': seat.status,
            'occupant': seat.occupant,
            'department': seat.department
        } for seat in seats])

if __name__ == '__main__':
    # Check for required environment variables
    required_env_vars = ['TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN', 'TWILIO_PHONE_NUMBER']
    missing_vars = [var for var in required_env_vars if not os.environ.get(var)]

    if missing_vars:
        raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

    app.run(debug=True)
