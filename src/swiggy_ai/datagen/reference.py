"""Static reference data: cities, cuisines, dishes, status spellings, reason codes."""

# name, share of customers/restaurants/orders, base delivery minutes, pincode prefix
CITIES = [
    ("Bengaluru", 0.22, 36, "560"),
    ("Mumbai", 0.22, 38, "400"),
    ("Delhi NCR", 0.20, 40, "110"),
    ("Hyderabad", 0.14, 34, "500"),
    ("Chennai", 0.12, 33, "600"),
    ("Pune", 0.10, 31, "411"),
]
CITY_NAMES = [c[0] for c in CITIES]
CITY_SHARES = [c[1] for c in CITIES]
CITY_BASE_MINUTES = [c[2] for c in CITIES]
CITY_PIN_PREFIX = [c[3] for c in CITIES]
MUMBAI = CITY_NAMES.index("Mumbai")

SEGMENTS = ["new", "regular", "loyal", "vip"]

# cuisine -> (share of restaurants, base price in INR, [(dish, category, price multiplier)])
CUISINES = {
    "North Indian": (0.22, 85, [
        ("Paneer Butter Masala", "Main Course", 1.5), ("Dal Makhani", "Main Course", 1.3),
        ("Butter Naan", "Breads", 0.5), ("Chicken Tikka", "Starters", 1.6),
        ("Jeera Rice", "Rice", 0.9), ("Chole Bhature", "Main Course", 1.1),
        ("Sweet Lassi", "Beverages", 0.7), ("Gulab Jamun", "Desserts", 0.6)]),
    "South Indian": (0.12, 65, [
        ("Masala Dosa", "Main Course", 1.2), ("Idli Sambar", "Main Course", 0.9),
        ("Medu Vada", "Starters", 0.8), ("Onion Uttapam", "Main Course", 1.1),
        ("Filter Coffee", "Beverages", 0.5), ("Ven Pongal", "Main Course", 0.9),
        ("Curd Rice", "Rice", 0.8), ("Payasam", "Desserts", 0.7)]),
    "Chinese": (0.12, 85, [
        ("Veg Hakka Noodles", "Main Course", 1.2), ("Chilli Chicken", "Starters", 1.5),
        ("Veg Manchurian", "Starters", 1.2), ("Fried Rice", "Rice", 1.1),
        ("Spring Rolls", "Starters", 0.9), ("Hot and Sour Soup", "Starters", 0.8),
        ("Schezwan Noodles", "Main Course", 1.3), ("Honey Chilli Potato", "Sides", 1.0)]),
    "Biryani": (0.12, 100, [
        ("Chicken Dum Biryani", "Main Course", 1.8), ("Mutton Biryani", "Main Course", 2.4),
        ("Veg Biryani", "Main Course", 1.4), ("Egg Biryani", "Main Course", 1.5),
        ("Onion Raita", "Sides", 0.5), ("Chicken 65", "Starters", 1.4),
        ("Mirchi Ka Salan", "Sides", 0.7), ("Double Ka Meetha", "Desserts", 0.7)]),
    "Pizza": (0.08, 110, [
        ("Margherita Pizza", "Main Course", 1.6), ("Farmhouse Pizza", "Main Course", 2.0),
        ("Pepperoni Pizza", "Main Course", 2.3), ("Garlic Bread", "Sides", 0.9),
        ("Cheesy Dip", "Sides", 0.3), ("Pasta Alfredo", "Main Course", 1.7),
        ("Coke", "Beverages", 0.4), ("Choco Lava Cake", "Desserts", 0.7)]),
    "Burgers": (0.08, 80, [
        ("Aloo Tikki Burger", "Main Course", 0.9), ("Chicken Zinger Burger", "Main Course", 1.5),
        ("Cheese Fries", "Sides", 0.9), ("Peri Peri Wings", "Starters", 1.6),
        ("Veg Wrap", "Main Course", 1.0), ("Chicken Wrap", "Main Course", 1.3),
        ("Thick Shake", "Beverages", 1.2), ("Walnut Brownie", "Desserts", 0.9)]),
    "Desserts": (0.05, 90, [
        ("Chocolate Truffle Cake", "Desserts", 2.0), ("Red Velvet Pastry", "Desserts", 1.2),
        ("Ice Cream Sundae", "Desserts", 1.3), ("Rasmalai", "Desserts", 1.1),
        ("Brownie Sundae", "Desserts", 1.5), ("Belgian Waffle", "Desserts", 1.4),
        ("Cheesecake", "Desserts", 1.6), ("Cold Coffee", "Beverages", 1.0)]),
    "Healthy": (0.05, 120, [
        ("Quinoa Salad", "Main Course", 1.4), ("Grilled Chicken Bowl", "Main Course", 1.9),
        ("Paneer Tikka Bowl", "Main Course", 1.7), ("Green Smoothie", "Beverages", 1.1),
        ("Protein Wrap", "Main Course", 1.5), ("Oats Bowl", "Main Course", 1.0),
        ("Fruit Salad", "Desserts", 0.9), ("Cold Pressed Juice", "Beverages", 1.2)]),
    "Street Food": (0.10, 55, [
        ("Pav Bhaji", "Main Course", 1.3), ("Vada Pav", "Starters", 0.5),
        ("Pani Puri", "Starters", 0.6), ("Bhel Puri", "Starters", 0.7),
        ("Samosa", "Starters", 0.5), ("Kathi Roll", "Main Course", 1.2),
        ("Steamed Momos", "Starters", 1.1), ("Masala Chai", "Beverages", 0.3)]),
    "Continental": (0.06, 140, [
        ("Grilled Fish", "Main Course", 2.2), ("Caesar Salad", "Starters", 1.3),
        ("Pasta Arrabbiata", "Main Course", 1.5), ("Club Sandwich", "Main Course", 1.3),
        ("Mushroom Soup", "Starters", 0.9), ("Steak Platter", "Main Course", 2.8),
        ("Garlic Mashed Potato", "Sides", 0.9), ("Tiramisu", "Desserts", 1.4)]),
}
CUISINE_NAMES = list(CUISINES)
FAST_GROWING_CUISINE = "Healthy"

# variant label -> price multiplier (menu item = dish x variant)
VARIANTS = [("", 1.0), ("Large", 1.35), ("Spicy", 1.05), ("Combo", 1.6),
            ("Mini", 0.7), ("Special", 1.2), ("Jumbo", 1.8), ("Classic", 0.9)]

RESTAURANT_SUFFIXES = ["Kitchen", "House", "Cafe", "Tandoor", "Bites", "Dhaba", "Express", "Corner"]

# internal status codes -> canonical (clean) value
STATUS_CANONICAL = ["delivered", "cancelled", "refunded", "failed"]

# how each status is spelled by the app (text, probability); the first is the clean spelling
STATUS_SPELLINGS = {
    "delivered": [("delivered", .45), ("Delivered", .35), ("DELIVERED", .12), ("DLVRD", .08)],
    "cancelled": [("cancelled", .40), ("Cancelled", .30), ("CANCELED", .20), ("canceled", .10)],
    "refunded": [("refunded", .50), ("Refunded", .30), ("REFUNDED", .20)],
    "failed": [("failed", .50), ("Failed", .30), ("FAILED", .20)],
}

REFUND_REASONS = [("LATE_DELIVERY", .30), ("MISSING_ITEM", .25), ("WRONG_ITEM", .20),
                  ("QUALITY_ISSUE", .245), ("SPOILED_FOOD", .005)]
SPIKE_REASON = "SPOILED_FOOD"

PAYMENT_METHODS = [("upi", .55), ("card", .20), ("cod", .12), ("wallet", .13)]

# hour of day (IST) -> relative order volume: lunch 12-14 and dinner 19-22 peak
HOUR_WEIGHTS = [0.3, 0.1, 0.05, 0.05, 0.05, 0.1, 0.3, 0.8, 1.5, 2, 2.5, 4,
                8, 9, 6, 3.5, 3, 3.5, 5, 8, 10, 9, 5, 1.5]
PEAK_HOURS = {12, 13, 14, 19, 20, 21, 22}

DAY_OF_WEEK_LIFT = [0.90, 0.90, 0.92, 0.95, 1.10, 1.30, 1.30]  # Monday first
