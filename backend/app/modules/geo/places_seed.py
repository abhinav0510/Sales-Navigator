"""
Seed rows for the built-in place table (`geo_place`).

This is a starter set that covers the target market (all Indian states and
union territories, ~55 Indian cities, a few global markets). In production the
operator loads the full GeoNames dump (cities > 5,000 people) with the same
columns; the resolver code does not change.

Row formats:
    COUNTRIES: (iso2, name, lat, lon, population, aliases)
    STATES:    (admin1_code, name, country_iso2, aliases)
    CITIES:    (name, admin1_code, country_iso2, lat, lon, population, aliases)
"""
from __future__ import annotations

COUNTRIES = [
    ("IN", "India", 22.35, 78.67, 1_430_000_000, ["bharat"]),
    ("US", "United States", 39.83, -98.58, 335_000_000, ["usa", "united states of america", "america"]),
    ("GB", "United Kingdom", 54.0, -2.0, 68_000_000, ["uk", "britain", "great britain", "england"]),
    ("AE", "United Arab Emirates", 24.0, 54.0, 9_500_000, ["uae", "emirates"]),
    ("SG", "Singapore", 1.35, 103.82, 5_900_000, []),
    ("DE", "Germany", 51.16, 10.45, 84_000_000, ["deutschland"]),
    ("AU", "Australia", -25.27, 133.78, 26_000_000, []),
    ("CA", "Canada", 56.13, -106.35, 40_000_000, []),
    ("FR", "France", 46.23, 2.21, 68_000_000, []),
    ("NL", "Netherlands", 52.13, 5.29, 17_800_000, ["holland"]),
    ("SA", "Saudi Arabia", 23.89, 45.08, 36_000_000, ["ksa"]),
    ("JP", "Japan", 36.2, 138.25, 125_000_000, []),
]

STATES = [
    ("IN-AN", "Andaman and Nicobar Islands", "IN", []), ("IN-AP", "Andhra Pradesh", "IN", ["ap"]),
    ("IN-AR", "Arunachal Pradesh", "IN", []), ("IN-AS", "Assam", "IN", []),
    ("IN-BR", "Bihar", "IN", []), ("IN-CH", "Chandigarh", "IN", []),
    ("IN-CG", "Chhattisgarh", "IN", []), ("IN-DH", "Dadra and Nagar Haveli and Daman and Diu", "IN", []),
    ("IN-DL", "Delhi", "IN", ["nct of delhi", "new delhi"]), ("IN-GA", "Goa", "IN", []),
    ("IN-GJ", "Gujarat", "IN", []), ("IN-HR", "Haryana", "IN", []),
    ("IN-HP", "Himachal Pradesh", "IN", ["hp"]), ("IN-JK", "Jammu and Kashmir", "IN", ["j&k", "kashmir"]),
    ("IN-JH", "Jharkhand", "IN", []), ("IN-KA", "Karnataka", "IN", []),
    ("IN-KL", "Kerala", "IN", []), ("IN-LA", "Ladakh", "IN", []),
    ("IN-LD", "Lakshadweep", "IN", []), ("IN-MP", "Madhya Pradesh", "IN", ["mp"]),
    ("IN-MH", "Maharashtra", "IN", []), ("IN-MN", "Manipur", "IN", []),
    ("IN-ML", "Meghalaya", "IN", []), ("IN-MZ", "Mizoram", "IN", []),
    ("IN-NL", "Nagaland", "IN", []), ("IN-OD", "Odisha", "IN", ["orissa"]),
    ("IN-PY", "Puducherry", "IN", ["pondicherry"]), ("IN-PB", "Punjab", "IN", []),
    ("IN-RJ", "Rajasthan", "IN", []), ("IN-SK", "Sikkim", "IN", []),
    ("IN-TN", "Tamil Nadu", "IN", ["tn"]), ("IN-TG", "Telangana", "IN", []),
    ("IN-TR", "Tripura", "IN", []), ("IN-UP", "Uttar Pradesh", "IN", ["up"]),
    ("IN-UK", "Uttarakhand", "IN", ["uttaranchal"]), ("IN-WB", "West Bengal", "IN", ["wb"]),
    ("US-CA", "California", "US", []), ("US-NY", "New York State", "US", []),
    ("US-TX", "Texas", "US", []), ("GB-ENG", "England", "GB", []),
]

CITIES = [
    # Maharashtra
    ("Mumbai", "IN-MH", "IN", 19.076, 72.8777, 12_400_000, ["bombay"]),
    ("Pune", "IN-MH", "IN", 18.5204, 73.8567, 3_100_000, ["poona"]),
    ("Nagpur", "IN-MH", "IN", 21.1458, 79.0882, 2_400_000, []),
    ("Thane", "IN-MH", "IN", 19.2183, 72.9781, 1_800_000, []),
    ("Nashik", "IN-MH", "IN", 19.9975, 73.7898, 1_500_000, ["nasik"]),
    ("Aurangabad", "IN-MH", "IN", 19.8762, 75.3433, 1_200_000, ["chhatrapati sambhajinagar"]),
    ("Kolhapur", "IN-MH", "IN", 16.705, 74.2433, 550_000, []),
    ("Solapur", "IN-MH", "IN", 17.6599, 75.9064, 950_000, []),
    ("Navi Mumbai", "IN-MH", "IN", 19.033, 73.0297, 1_100_000, []),
    # Karnataka
    ("Bengaluru", "IN-KA", "IN", 12.9716, 77.5946, 8_400_000, ["bangalore"]),
    ("Mysuru", "IN-KA", "IN", 12.2958, 76.6394, 900_000, ["mysore"]),
    ("Mangaluru", "IN-KA", "IN", 12.9141, 74.856, 620_000, ["mangalore"]),
    ("Hubballi", "IN-KA", "IN", 15.3647, 75.124, 940_000, ["hubli"]),
    # Tamil Nadu / Telangana / Andhra
    ("Chennai", "IN-TN", "IN", 13.0827, 80.2707, 7_100_000, ["madras"]),
    ("Coimbatore", "IN-TN", "IN", 11.0168, 76.9558, 1_600_000, []),
    ("Madurai", "IN-TN", "IN", 9.9252, 78.1198, 1_000_000, []),
    ("Hyderabad", "IN-TG", "IN", 17.385, 78.4867, 6_800_000, []),
    ("Warangal", "IN-TG", "IN", 17.9689, 79.5941, 700_000, []),
    ("Visakhapatnam", "IN-AP", "IN", 17.6868, 83.2185, 2_000_000, ["vizag"]),
    ("Vijayawada", "IN-AP", "IN", 16.5062, 80.648, 1_100_000, []),
    # North
    ("Delhi", "IN-DL", "IN", 28.6139, 77.209, 16_800_000, ["new delhi"]),
    ("Gurugram", "IN-HR", "IN", 28.4595, 77.0266, 1_100_000, ["gurgaon"]),
    ("Faridabad", "IN-HR", "IN", 28.4089, 77.3178, 1_400_000, []),
    ("Noida", "IN-UP", "IN", 28.5355, 77.391, 640_000, []),
    ("Ghaziabad", "IN-UP", "IN", 28.6692, 77.4538, 1_700_000, []),
    ("Lucknow", "IN-UP", "IN", 26.8467, 80.9462, 2_800_000, []),
    ("Kanpur", "IN-UP", "IN", 26.4499, 80.3319, 2_800_000, []),
    ("Varanasi", "IN-UP", "IN", 25.3176, 82.9739, 1_200_000, ["benares", "banaras"]),
    ("Jaipur", "IN-RJ", "IN", 26.9124, 75.7873, 3_000_000, []),
    ("Udaipur", "IN-RJ", "IN", 24.5854, 73.7125, 450_000, []),
    ("Jodhpur", "IN-RJ", "IN", 26.2389, 73.0243, 1_000_000, []),
    ("Chandigarh", "IN-CH", "IN", 30.7333, 76.7794, 1_000_000, []),
    ("Ludhiana", "IN-PB", "IN", 30.901, 75.8573, 1_600_000, []),
    ("Amritsar", "IN-PB", "IN", 31.634, 74.8723, 1_100_000, []),
    ("Dehradun", "IN-UK", "IN", 30.3165, 78.0322, 580_000, []),
    ("Shimla", "IN-HP", "IN", 31.1048, 77.1734, 170_000, []),
    ("Srinagar", "IN-JK", "IN", 34.0837, 74.7973, 1_200_000, []),
    # West / Central
    ("Ahmedabad", "IN-GJ", "IN", 23.0225, 72.5714, 5_600_000, ["amdavad"]),
    ("Surat", "IN-GJ", "IN", 21.1702, 72.8311, 4_500_000, []),
    ("Vadodara", "IN-GJ", "IN", 22.3072, 73.1812, 1_700_000, ["baroda"]),
    ("Rajkot", "IN-GJ", "IN", 22.3039, 70.8022, 1_300_000, []),
    ("Indore", "IN-MP", "IN", 22.7196, 75.8577, 2_000_000, []),
    ("Bhopal", "IN-MP", "IN", 23.2599, 77.4126, 1_800_000, []),
    ("Raipur", "IN-CG", "IN", 21.2514, 81.6296, 1_000_000, []),
    ("Panaji", "IN-GA", "IN", 15.4909, 73.8278, 115_000, ["panjim"]),
    # East / North-East / South
    ("Kolkata", "IN-WB", "IN", 22.5726, 88.3639, 4_500_000, ["calcutta"]),
    ("Patna", "IN-BR", "IN", 25.5941, 85.1376, 1_700_000, []),
    ("Aurangabad", "IN-BR", "IN", 24.7521, 84.3742, 100_000, []),   # deliberately ambiguous with MH
    ("Ranchi", "IN-JH", "IN", 23.3441, 85.3096, 1_100_000, []),
    ("Bhubaneswar", "IN-OD", "IN", 20.2961, 85.8245, 840_000, []),
    ("Guwahati", "IN-AS", "IN", 26.1445, 91.7362, 960_000, []),
    ("Kochi", "IN-KL", "IN", 9.9312, 76.2673, 600_000, ["cochin"]),
    ("Thiruvananthapuram", "IN-KL", "IN", 8.5241, 76.9366, 950_000, ["trivandrum"]),
    ("Kozhikode", "IN-KL", "IN", 11.2588, 75.7804, 610_000, ["calicut"]),
    # Global markets
    ("New York", "US-NY", "US", 40.7128, -74.006, 8_300_000, ["nyc", "new york city"]),
    ("San Francisco", "US-CA", "US", 37.7749, -122.4194, 810_000, ["sf"]),
    ("London", "GB-ENG", "GB", 51.5074, -0.1278, 8_900_000, []),
    ("Dubai", None, "AE", 25.2048, 55.2708, 3_600_000, []),
    ("Berlin", None, "DE", 52.52, 13.405, 3_700_000, []),
]

# Named groups that expand to several places (resolved as a list of scopes).
GROUPS: dict[str, list[str]] = {
    "ncr": ["Delhi", "Gurugram", "Noida", "Ghaziabad", "Faridabad"],
    "delhi ncr": ["Delhi", "Gurugram", "Noida", "Ghaziabad", "Faridabad"],
    "tier 1 india": ["Mumbai", "Delhi", "Bengaluru", "Hyderabad", "Chennai", "Kolkata", "Pune", "Ahmedabad"],
    "gcc": ["United Arab Emirates", "Saudi Arabia"],
}

# For "global": markets ranked for an India-based seller (editable by the user).
GLOBAL_MARKET_ORDER = ["IN", "US", "GB", "AE", "SG", "AU", "CA", "DE"]
