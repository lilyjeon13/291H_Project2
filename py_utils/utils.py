from openroad import Design, Tech, Timing
from odb import *
from pathlib import Path
import sys
import argparse
import pdn, odb, utl
import time


def lib_unit_consistency(design) -> bool:
    lib = design.getDb().getLibs()[0]
    dbu_per_micron = lib.getDbUnitsPerMicron()
    cam_vertical_offset = lib.getSites()[0].getHeight()
    for i in range(1, len(design.getDb().getLibs())):
        lib = design.getDb().getLibs()[i]
        if (dbu_per_micron != lib.getDbUnitsPerMicron()):
            return False
        if (cam_vertical_offset != lib.getSites()[0].getHeight()):
            return False
        dbu_per_micron = lib.getDbUnitsPerMicron()
        cam_vertical_offset = lib.getSites()[0].getHeight()
    return True


def load_design(techNode, floorplanOdbFile, sdcFile, flow_path):
  tech = Tech()
  platform_dir = flow_path + "/platforms/" + techNode + "/"
  libDir = Path(platform_dir + "lib/")
  lefDir = Path(platform_dir + "lef/")
  rcFile = platform_dir + "setRC.tcl"

  print("libDir: ", libDir)
  print("lefDir: ", lefDir)
  print("rcFile: ", rcFile)

  # Read technology files
  libFiles = libDir.glob('*.lib')
  lefFiles = lefDir.glob('*.lef')
  for libFile in libFiles:
    print("Reading library file: %s\n" % libFile)
    tech.readLiberty(libFile.as_posix())
  
  techLefFiles = lefDir.glob("*tech*.lef")
  for techLefFile in techLefFiles:
    tech.readLef(techLefFile.as_posix())
  for lefFile in lefFiles:
    tech.readLef(lefFile.as_posix())
  
  design = Design(tech)

  # read the odb file
  design.readDb(floorplanOdbFile)
  design.evalTclString("read_sdc %s"%sdcFile)
  design.evalTclString("source " + rcFile)

  return tech, design


def get_connection(large_net_threshold, file_name, IO_map, inst_map):
  f = open(file_name, "a")
  f.write("Nets information (Each line represents one net. The first element in each line is the driver pin.):\n")
  block = ord.get_db_block()
  nets = block.getNets()
  for net in nets:
    if net.getName() == "VDD" or net.getName() == "VSS":
      continue
    if (len(net.getITerms()) + len(net.getBTerms()) >= large_net_threshold):
      continue

    sinkPins = []
    driverId = -1
    dPin = None
    # check the instance pins
    for p in net.getITerms():
      if p.isOutputSignal():
        dPin = p
        driverId = inst_map[p.getInst().getName()]
      else:
        sinkPins.append(inst_map[p.getInst().getName()])
    
    # check the IO pins
    for p in net.getBTerms():
      if dPin is None and p.getIoType() == "INPUT":
        dPin = p
        driverId = IO_map[p.getName()]
      else:
        sinkPins.append(IO_map[p.getName()])
      
    if dPin is None:
      if (net.getName() == "VDD" or net.getName() == "VSS"):
        continue  # ignore power and ground nets
      print("No driver found for net: ",net.getName())
      continue
   
    if (len(sinkPins) + 1 >= large_net_threshold):
      print("Ignore large net: ",net.getName())
      continue

    if (len(sinkPins) == 0):
      continue

    f.write(str(driverId) + " ")
    for sinkId in sinkPins:
      f.write(str(sinkId) + " ")
    f.write("\n")
  f.write("*********************************************************************************************\n")
  f.close()


def get_registers(design):
    registers = []
    regs_ptr = design.evalTclString("::sta::all_register").split()
    for reg in regs_ptr:
        reg_db_ptr = design.evalTclString("::sta::sta_to_db_inst "+reg)
        if reg_db_ptr != "NULL":
            reg_inst_name = design.evalTclString(reg_db_ptr+" getName")
            registers.append(reg_inst_name)
    return registers


def get_clknets(design):
    clk_nets = []
    clk_nets_ptr = design.evalTclString("::sta::find_all_clk_nets").split()
    clk_nets = [design.evalTclString(x+" getName") for x in clk_nets_ptr]
    return clk_nets


def get_insts(design, inst_map, file_name, vertex_id):
  block = ord.get_db_block()
  insts = block.getInsts()
  registers = get_registers(design)
  f = open(file_name, "a")
  f.write("Instance information (Each line represents one instance: vertex_id, instance_name, cell_name, isMacro, isSeq, isFixed, x_center, y_center, width, height):\n")
  for inst in insts:
    instName = inst.getName()
    master = inst.getMaster()
    masterName = master.getName()
    BBox = inst.getBBox()
    isMacro = master.isBlock()
    isSeq = True if instName in registers else False
    isFixed = True if inst.isFixed() else False
    lx = BBox.xMin()
    ly = BBox.yMin()
    ux = BBox.xMax()
    uy = BBox.yMax()
    width = ux - lx
    height = uy - ly
    x_center = (lx + ux) / 2
    y_center = (ly + uy) / 2
    isFiller = True if master.isFiller() else False
    isTapCell = True if ("TAPCELL" in masterName or "tapcell" in masterName) else False
    #isBuffer = 1 if design.isBuffer(master) else 0
    #isInverter = 1 if design.isInverter(master) else 0
    if (isFiller == True or isTapCell == True):
      continue # ignore filler and tap cells
    f.write(str(vertex_id) + " ")
    f.write(instName + " ")
    f.write(masterName + " ")
    f.write(str(isMacro) + " ")
    f.write(str(isSeq) + " ")
    f.write(str(isFixed) + " ")
    if (isMacro == True or isFixed == True):
      f.write(str(x_center) + " ")
      f.write(str(y_center) + " ")
    else:
      f.write(str(-1) + " ")
      f.write(str(-1) + " ")
    f.write(str(width) + " ")
    f.write(str(height) + " ")
    f.write("\n")
    inst_map[instName] = vertex_id
    vertex_id += 1
  f.write("*********************************************************************************************\n")
  f.close()     


### This function is only for testing purpose
### Please replace this function with your own function
def generate_init_placement(design, file_name):
  f = open(file_name, "w")
  block = ord.get_db_block()
  insts = block.getInsts()
  registers = get_registers(design)
  for inst in insts:
    instName = inst.getName()
    master = inst.getMaster()
    masterName = master.getName()
    BBox = inst.getBBox()
    isMacro = master.isBlock()
    isSeq = True if instName in registers else False
    isFixed = True if inst.isFixed() else False
    lx = BBox.xMin()
    ly = BBox.yMin()
    ux = BBox.xMax()
    uy = BBox.yMax()
    width = ux - lx
    height = uy - ly
    x_center = (lx + ux) / 2
    y_center = (ly + uy) / 2
    isFiller = True if master.isFiller() else False
    isTapCell = True if ("TAPCELL" in masterName or "tapcell" in masterName) else False
    #isBuffer = 1 if design.isBuffer(master) else 0
    #isInverter = 1 if design.isInverter(master) else 0
    if (isFiller == True or isTapCell == True):
      continue # ignore filler and tap cells
    f.write(instName + " ")
    if (isMacro == True or isFixed == True):
      continue
    else:
      f.write(str(int(x_center)) + " ")
      f.write(str(int(y_center)) + " ")
    f.write("\n")
  f.close()     




def load_init_placement(file_name):
  with open(file_name, "r") as f:
    content = f.read().splitlines()
  f.close()

  block = ord.get_db_block()
  for line in content:
    items = line.split(" ")
    if len(items) < 3:
      continue

    instName = items[0]
    x = int(float(items[1]))
    y = int(float(items[2]))

    # Set the position of the instance    
    inst = block.findInst(instName)
    inst.setLocation(x, y)


def run_incremental_placement(design, block, tech, odb_dir):
  # Configure and run global placement
  print("###run global placement###")
  design.evalTclString("global_placement -routability_driven -timing_driven -skip_initial_place -incremental")

  print("Please use the generated 3_3_place_gp.def and 3_3_place_gp.odb files for remaining flows.")
  design.writeDef(f"{odb_dir}/3_3_place_gp.def")
  design.writeDb(f"{odb_dir}/3_3_place_gp.odb")

  # Run initial detailed placement
  site = design.getBlock().getRows()[0].getSite()
  # entire site containing power rails and everything
  max_disp_x = int((design.getBlock().getBBox().xMax() - design.getBlock().getBBox().xMin()) / site.getWidth())
  max_disp_y = int((design.getBlock().getBBox().yMax() - design.getBlock().getBBox().yMin()) / site.getHeight())
  print("The following files are just for testing purpose. Please ignore them.")
  print("###run legalization###")
  design.getOpendp().detailedPlacement(max_disp_x, max_disp_y, "")
  
  design.writeDef(f"{odb_dir}/3_5_place_dp.def")
  design.writeDb(f"{odb_dir}/3_5_place_dp.odb")

def get_IO_pins(IO_map, file_name):
  f = open(file_name, "a")
  f.write("Input/Output Pin information (Each line represents one fixed pin: vertex_id, IO_name, IO_type, x_center, y_center):\n")
  block = ord.get_db_block()
  BTerms = block.getBTerms()
  vertex_id = 0
  for bTerm in BTerms:
    BBox = bTerm.getBBox()
    x_center = (BBox.xMin() + BBox.xMax()) / 2
    y_center = (BBox.yMin() + BBox.yMax()) / 2
    f.write(str(vertex_id) + " ")
    f.write(bTerm.getName() + " ")
    f.write(bTerm.getIoType() + " ")
    f.write(str(int(x_center)) + " ")
    f.write(str(int(y_center)) + "\n")
    IO_map[bTerm.getName()] = vertex_id
    vertex_id += 1
  f.write("*********************************************************************************************\n")
  f.close()



def get_basic_info(file_name):
  block = ord.get_db_block()
  design_name = block.getName()
  dbunits = block.getDbUnitsPerMicron()
  die_width = block.getDieArea().dx() 
  die_height = block.getDieArea().dy() 
  core_width = block.getCoreArea().dx() 
  core_height = block.getCoreArea().dy() 
  nets = block.getNets()
  insts = block.getInsts()

  f = open(file_name, "a")
  f.write("*********************************************************************************************\n")
  f.write("Basic information of the design:\n")
  f.write("Design name: %s\n"%design_name)
  #f.write("Number of nets: %d\n"%len(nets))
  #f.write("Number of instances: %d\n"%len(insts))
  f.write("UNITS DISTANCE MICRONS : %d (We use DBU to store the layout information)\n"%dbunits)  
  f.write("Die width: %d DBU\n"%die_width)
  f.write("Die height: %d DBU\n"%die_height)
  f.write("Core width: %d DBU\n"%core_width)
  f.write("Core height: %d DBU\n"%core_height)
  f.write("Core region:  lx = %d, ly = %d, ux = %d, uy = %d\n"%(block.getCoreArea().xMin(),
                                                            block.getCoreArea().yMin(),
                                                            block.getCoreArea().xMax(),
                                                            block.getCoreArea().yMax()))
  f.write("*********************************************************************************************\n")
  f.close()


if __name__ == "__main__":
    # You can run this script in this manner:  openroad -python python_read_design.py
    parser = argparse.ArgumentParser(description="Example script to perform global placement initialization using OpenROAD.")
    parser.add_argument("-d", default="ibex", help="Give the design name")
    parser.add_argument("-t", default="nangate45", help="Give the technology node")
    parser.add_argument("-large_net_threshold", default="1000", help="Large net threshold. We should remove global nets like reset.")
    parser.add_argument("-flow_path", default="./of/flow", help="path of flow directory")
    
    args = parser.parse_args()

    tech_node = args.t
    design = args.d
    large_net_threshold = int(args.large_net_threshold)
    hg_file_name = str(design) + "_" + str(tech_node) + ".txt"
    f = open(hg_file_name, "w")
    f.close()

    flow_path = args.flow_path
    path = flow_path + "/results/" + tech_node + "/" + design + "/base"
    floorplan_odb_file = path + "/3_2_place_iop.odb"
    sdc_file = path + "/2_floorplan.sdc"
    # Load the design
    tech, design = load_design(tech_node, floorplan_odb_file, sdc_file)
    # Get basic information
    get_basic_info(hg_file_name)

    # get all the IO pins
    IO_map = {}
    inst_map = {}
    get_IO_pins(IO_map, hg_file_name) 

    # get all the instances
    vertex_id = len(IO_map)
    get_insts(design, inst_map, hg_file_name, vertex_id)

    # get all the connections
    get_connection(large_net_threshold, hg_file_name, IO_map, inst_map)      

    # from here.  You should get the hypergraph file
    
    # Please write your own function to generate the initial placement file
    #init_placement_file = "init_placement.txt"
    #generate_init_placement(design, init_placement_file)

    # Load the initial placement and run incremental placement    
    load_init_placement("init_placement_test.txt")

    run_incremental_placement(design)
    print("Finished running global placement and detailed placement.")
    print("\n")
    print("*************************************************************************************************")
    print("Please use the generated 3_3_place_gp.def and 3_3_place_gp.odb files for remaining flows.")
    print("You can use OpenROAD GUI to visualize the placement: openroad -gui")
    print("After opening the OpenROAD GUI, then go to File -> Open DB and select the 3_3_place_gp.odb file.")
    print("*************************************************************************************************")
    print("Good luck with your project!")
    print("*************************************************************************************************")

