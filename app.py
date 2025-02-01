from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///office_seats.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


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


with app.app_context():
    db.create_all()


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

        dept_data = []
        for dept, count in zip(departments, counts):
            if dept and count.isdigit():
                dept_data.append((dept, int(count)))

        total_requested = sum(count for _, count in dept_data)
        total_available = Seat.query.filter_by(status='available').count()

        if total_available < total_requested:
            return "Not enough seats available. Required: {}, Available: {}".format(total_requested,
                                                                                    total_available), 400

        allocation_report = {}
        offices = Office.query.order_by(Office.id).all()

        for department, required in dept_data:
            allocated = 0
            allocation_report[department] = {}

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
                db.session.commit()

                allocation_report[department][office.name] = num_allocated
                allocated += num_allocated

                if allocated >= required:
                    break

        return render_template('report.html', report=allocation_report)
    return render_template('allocate.html')


@app.route('/api/offices', methods=['GET', 'POST'])
def handle_offices():
    if request.method == 'POST':
        data = request.get_json()
        new_office = Office(
            name=data['name'],
            location=data['location'],
            capacity=data['capacity']
        )
        db.session.add(new_office)
        db.session.commit()

        # Create 50 seats for the new office
        for i in range(1, 51):
            seat = Seat(
                office_id=new_office.id,
                seat_number=str(i),
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
        new_seat = Seat(
            office_id=office_id,
            seat_number=data['seat_number'],
            status=data.get('status', 'available')
        )
        db.session.add(new_seat)
        db.session.commit()
        return jsonify({'id': new_seat.id}), 201
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
    app.run(debug=True)