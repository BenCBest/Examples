# Import python libraries
import os
import numpy as np

# Importing the Kratos Library
import KratosMultiphysics
import KratosMultiphysics.MultilevelMonteCarloApplication
import KratosMultiphysics.MappingApplication

# Importing the problem analysis stage class
from FluidDynamicsAnalysisMC import FluidDynamicsAnalysisMC
from KratosMultiphysics.FluidDynamicsApplication import check_and_prepare_model_process_fluid

# Avoid printing of Kratos informations
KratosMultiphysics.Logger.GetDefaultOutput().SetSeverity(KratosMultiphysics.Logger.Severity.WARNING)


class SimulationScenario(FluidDynamicsAnalysisMC):
    def __init__(self,input_model,input_parameters,sample):
        super(SimulationScenario,self).__init__(input_model,input_parameters)
        self.sample = sample
        self.mapping = False
        self.interest_model_part = "FluidModelPart.NoSlip3D_structure"
        self.number_instances_time_power_sums = 0
        self.IsVelocityFieldPerturbed = False
        self.filename = "filename"
        print("[SCREENING] burn-in coefficient simulation scenario",self.burnin_time_coefficient)

    def ModifyInitialProperties(self):
        """
        function changing print to file settings
        input:  self: an instance of the class
        """
        super(SimulationScenario,self).ModifyInitialProperties()
        # add capability of saving force time series
        filename = self.filename
        self.project_parameters["processes"]["auxiliar_process_list"][0]["Parameters"]["write_drag_output_file"].SetBool(True)
        self.project_parameters["processes"]["auxiliar_process_list"][0]["Parameters"]["output_file_settings"]["file_name"].SetString(filename)
        self.project_parameters["processes"]["auxiliar_process_list"][0]["Parameters"]["output_file_settings"]["folder_name"].SetString("")

    def ApplyBoundaryConditions(self):
        """
        function introducing the stochasticity in the problem
        input:  self: an instance of the class
        """
        super(SimulationScenario,self).ApplyBoundaryConditions()
        if (self.IsVelocityFieldPerturbed is False) and (self.project_parameters["problem_data"]["perturbation"]["type"].GetString() == "uncorrelated"):
            np.random.seed(self.sample[0])
            print("[SCREENING] perturbing the domain:","Yes")
            self.main_model_part = self.model.GetModelPart("FluidModelPart")
            # load velocity field
            with open("average_velocity_field_CAARC_3d_combinedPressureVelocity_312k_690.0.dat") as dat_file:
                lines=dat_file.readlines()
                for line, node in zip(lines, self.main_model_part.Nodes):
                    if not (node.IsFixed(KratosMultiphysics.VELOCITY_X) or node.IsFixed(KratosMultiphysics.VELOCITY_Y) or node.IsFixed(KratosMultiphysics.VELOCITY_Z) or node.IsFixed(KratosMultiphysics.PRESSURE)):
                        # retrieve velocity
                        velocity = KratosMultiphysics.Vector(3, 0.0)
                        velocity[0] = float(line.split(' ')[0])
                        velocity[1] = float(line.split(' ')[1])
                        velocity[2] = float(line.split(' ')[2])
                        # compute uncorrelated perturbation
                        perturbation_intensity = self.project_parameters["problem_data"]["perturbation"]["intensity"].GetDouble()
                        perturbation = np.random.uniform(-perturbation_intensity,perturbation_intensity,3) * velocity.norm_2() # all nodes and directions different value
                        # sum avg velocity and perturbation
                        velocity[0] = velocity[0] + perturbation[0]
                        velocity[1] = velocity[1] + perturbation[1]
                        velocity[2] = velocity[2] + perturbation[2]
                        node.SetSolutionStepValue(KratosMultiphysics.VELOCITY, 1, velocity)
                        node.SetSolutionStepValue(KratosMultiphysics.VELOCITY, velocity)
            self.IsVelocityFieldPerturbed = True
        else:
            print("[SCREENING] perturbing the domain:", "No")

    def ComputeNeighbourElements(self):
        """
        function computing neighbour elements, required by our boundary conditions
        input:  self: an instance of the class
        """
        tmoc = KratosMultiphysics.TetrahedralMeshOrientationCheck
        throw_errors = False
        flags = (tmoc.COMPUTE_NODAL_NORMALS).AsFalse() | (tmoc.COMPUTE_CONDITION_NORMALS).AsFalse()
        flags |= tmoc.ASSIGN_NEIGHBOUR_ELEMENTS_TO_CONDITIONS
        KratosMultiphysics.TetrahedralMeshOrientationCheck(self._GetSolver().main_model_part.GetSubModelPart("fluid_computational_model_part"),throw_errors, flags).Execute()

    def Initialize(self):
        """
        function initializing moment estimator array
        input:  self: an instance of the class
        """
        super(SimulationScenario,self).Initialize()
        # compute neighbour elements required for current boundary conditions and not automatically run due to remeshing
        self.ComputeNeighbourElements()
        # initialize moment estimator array for each qoi to build time power sums
        self.moment_estimator_array = [[[0.0],[0.0],[0.0],[0.0],[0.0],[0.0],[0.0],[0.0],[0.0],[0.0]] for _ in range (0,2)] # +2 is for drag force x and base moment z
        if (self.mapping is True):
            power_sums_parameters = KratosMultiphysics.Parameters("""{
                "reference_variable_name": "PRESSURE"
                }""")
            self.power_sums_process_mapping = KratosMultiphysics.MultilevelMonteCarloApplication.PowerSumsStatistics(self.mapping_reference_model.GetModelPart(self.interest_model_part),power_sums_parameters)
            self.power_sums_process_mapping.ExecuteInitialize()
            print("[SCREENING] number nodes of submodelpart + drag force x + base moment z:",self.mapping_reference_model.GetModelPart(self.interest_model_part).NumberOfNodes()+2) # +2 is for drag coefficient and base moment z
        else:
            power_sums_parameters = KratosMultiphysics.Parameters("""{
                "reference_variable_name": "PRESSURE"
                }""")
            self.power_sums_process = KratosMultiphysics.MultilevelMonteCarloApplication.PowerSumsStatistics(self.model.GetModelPart(self.interest_model_part),power_sums_parameters)
            self.power_sums_process.ExecuteInitialize()
            print("[SCREENING] number nodes of submodelpart + drag + base moment z:",self.model.GetModelPart(self.interest_model_part).NumberOfNodes()+2) # +2 is for drag coefficient and base moment z
        print("[SCREENING] mapping flag:",self.mapping)

    def FinalizeSolutionStep(self):
        """
        function applying mapping if required and updating moment estimator array
        input:  self: an instance of the class
        """
        super(SimulationScenario,self).FinalizeSolutionStep()
        # run if current index is index of interest
        if (self.is_current_index_maximum_index is True):
            # avoid burn-in time
            if (self.model.GetModelPart(self.interest_model_part).ProcessInfo.GetPreviousTimeStepInfo().GetValue(KratosMultiphysics.TIME) >= \
                self.burnin_time_coefficient*self.model.GetModelPart(self.interest_model_part).ProcessInfo.GetValue(KratosMultiphysics.END_TIME)):
                # update number of contributions to time power sums
                self.number_instances_time_power_sums = self.number_instances_time_power_sums + 1
                # update power sums of drag force x and base moment z
                self.moment_estimator_array[0][0][0] = self.moment_estimator_array[0][0][0] + self.current_drag_force_x
                self.moment_estimator_array[0][1][0] = self.moment_estimator_array[0][1][0] + self.current_drag_force_x**2
                self.moment_estimator_array[0][2][0] = self.moment_estimator_array[0][2][0] + self.current_drag_force_x**3
                self.moment_estimator_array[0][3][0] = self.moment_estimator_array[0][3][0] + self.current_drag_force_x**4
                self.moment_estimator_array[0][4][0] = self.moment_estimator_array[0][4][0] + self.current_drag_force_x**5
                self.moment_estimator_array[0][5][0] = self.moment_estimator_array[0][5][0] + self.current_drag_force_x**6
                self.moment_estimator_array[0][6][0] = self.moment_estimator_array[0][6][0] + self.current_drag_force_x**7
                self.moment_estimator_array[0][7][0] = self.moment_estimator_array[0][7][0] + self.current_drag_force_x**8
                self.moment_estimator_array[0][8][0] = self.moment_estimator_array[0][8][0] + self.current_drag_force_x**9
                self.moment_estimator_array[0][9][0] = self.moment_estimator_array[0][9][0] + self.current_drag_force_x**10
                self.moment_estimator_array[1][0][0] = self.moment_estimator_array[1][0][0] + self.current_base_moment_z
                self.moment_estimator_array[1][1][0] = self.moment_estimator_array[1][1][0] + self.current_base_moment_z**2
                self.moment_estimator_array[1][2][0] = self.moment_estimator_array[1][2][0] + self.current_base_moment_z**3
                self.moment_estimator_array[1][3][0] = self.moment_estimator_array[1][3][0] + self.current_base_moment_z**4
                self.moment_estimator_array[1][4][0] = self.moment_estimator_array[1][4][0] + self.current_base_moment_z**5
                self.moment_estimator_array[1][5][0] = self.moment_estimator_array[1][5][0] + self.current_base_moment_z**6
                self.moment_estimator_array[1][6][0] = self.moment_estimator_array[1][6][0] + self.current_base_moment_z**7
                self.moment_estimator_array[1][7][0] = self.moment_estimator_array[1][7][0] + self.current_base_moment_z**8
                self.moment_estimator_array[1][8][0] = self.moment_estimator_array[1][8][0] + self.current_base_moment_z**9
                self.moment_estimator_array[1][9][0] = self.moment_estimator_array[1][9][0] + self.current_base_moment_z**10
                if (self.mapping is True):
                    # mapping from current model part of interest to reference model part the pressure
                    mapping_parameters = KratosMultiphysics.Parameters("""{
                        "mapper_type": "nearest_element",
                        "interface_submodel_part_origin": "FluidModelPart.NoSlip3D_structure",
                        "interface_submodel_part_destination": "FluidModelPart.NoSlip3D_structure",
                        "echo_level" : 3
                        }""")
                    mapper = KratosMultiphysics.MappingApplication.MapperFactory.CreateMapper(self._GetSolver().main_model_part,self.mapping_reference_model.GetModelPart("FluidModelPart"),mapping_parameters)
                    mapper.Map(KratosMultiphysics.PRESSURE,KratosMultiphysics.PRESSURE)
                    # pressure field power sums
                    self.power_sums_process_mapping.ExecuteFinalizeSolutionStep()
                else:
                    # update pressure field power sums
                    self.power_sums_process.ExecuteFinalizeSolutionStep()
        else:
            pass

    def EvaluateQuantityOfInterest(self):
        """
        function evaluating the QoI of the problem: lift coefficient
        input:  self: an instance of the class
        """
        # run if current index is index of interest
        if (self.is_current_index_maximum_index is True):
            print("[SCREENING] computing qoi current index:",self.is_current_index_maximum_index)
            qoi_list = []
            # append time average drag coefficient
            qoi_list.append(self.mean_drag_force_x)
            # append time averaged base moment_z
            qoi_list.append(self.mean_base_moment_z)
            # append time average pressure
            if (self.mapping is not True):
                for node in self.model.GetModelPart(self.interest_model_part).Nodes:
                    qoi_list.append(node.GetValue(KratosMultiphysics.ExaquteSandboxApplication.PRESSURE_WEIGHTED))
            elif (self.mapping is True):
                for node in self.mapping_reference_model.GetModelPart(self.interest_model_part).Nodes:
                    qoi_list.append(node.GetValue(KratosMultiphysics.ExaquteSandboxApplication.PRESSURE_WEIGHTED))
            # append number of contributions to the power sums list
            self.moment_estimator_array[0].append(self.number_instances_time_power_sums) # drag force x
            self.moment_estimator_array[1].append(self.number_instances_time_power_sums) # base moment z
            # append drag force x and base moment z time series power sums
            qoi_list.append(self.moment_estimator_array[0]) # drag force x
            qoi_list.append(self.moment_estimator_array[1]) # base moment z
            # append pressure time series power sums
            if (self.mapping is True):
                for node in self.mapping_reference_model.GetModelPart(self.interest_model_part).Nodes:
                    S1 = node.GetValue(KratosMultiphysics.MultilevelMonteCarloApplication.POWER_SUM_1)
                    S2 = node.GetValue(KratosMultiphysics.MultilevelMonteCarloApplication.POWER_SUM_2)
                    S3 = node.GetValue(KratosMultiphysics.MultilevelMonteCarloApplication.POWER_SUM_3)
                    S4 = node.GetValue(KratosMultiphysics.MultilevelMonteCarloApplication.POWER_SUM_4)
                    S5 = node.GetValue(KratosMultiphysics.MultilevelMonteCarloApplication.POWER_SUM_5)
                    S6 = node.GetValue(KratosMultiphysics.MultilevelMonteCarloApplication.POWER_SUM_6)
                    S7 = node.GetValue(KratosMultiphysics.MultilevelMonteCarloApplication.POWER_SUM_7)
                    S8 = node.GetValue(KratosMultiphysics.MultilevelMonteCarloApplication.POWER_SUM_8)
                    S9 = node.GetValue(KratosMultiphysics.MultilevelMonteCarloApplication.POWER_SUM_9)
                    S10 = node.GetValue(KratosMultiphysics.MultilevelMonteCarloApplication.POWER_SUM_10)
                    M = self.number_instances_time_power_sums
                    power_sums = [[S1],[S2],[S3],[S4],[S5],[S6],[S7],[S8],[S9],[S10],M]
                    qoi_list.append(power_sums)
                assert (len(qoi_list) == \
                    2*(self.mapping_reference_model.GetModelPart(self.interest_model_part).NumberOfNodes()+2)) # +2 is for drag coefficient and base moment z
            else:
                for node in self.model.GetModelPart(self.interest_model_part).Nodes:
                    S1 = node.GetValue(KratosMultiphysics.MultilevelMonteCarloApplication.POWER_SUM_1)
                    S2 = node.GetValue(KratosMultiphysics.MultilevelMonteCarloApplication.POWER_SUM_2)
                    S3 = node.GetValue(KratosMultiphysics.MultilevelMonteCarloApplication.POWER_SUM_3)
                    S4 = node.GetValue(KratosMultiphysics.MultilevelMonteCarloApplication.POWER_SUM_4)
                    S5 = node.GetValue(KratosMultiphysics.MultilevelMonteCarloApplication.POWER_SUM_5)
                    S6 = node.GetValue(KratosMultiphysics.MultilevelMonteCarloApplication.POWER_SUM_6)
                    S7 = node.GetValue(KratosMultiphysics.MultilevelMonteCarloApplication.POWER_SUM_7)
                    S8 = node.GetValue(KratosMultiphysics.MultilevelMonteCarloApplication.POWER_SUM_8)
                    S9 = node.GetValue(KratosMultiphysics.MultilevelMonteCarloApplication.POWER_SUM_9)
                    S10 = node.GetValue(KratosMultiphysics.MultilevelMonteCarloApplication.POWER_SUM_10)
                    M = self.number_instances_time_power_sums
                    power_sums = [[S1],[S2],[S3],[S4],[S5],[S6],[S7],[S8],[S9],[S10],M]
                    qoi_list.append(power_sums)
                assert (len(qoi_list) \
                    == 2*(self.model.GetModelPart(self.interest_model_part).NumberOfNodes()+2)) # +2 is for drag force x and base moment z
        else:
            print("[SCREENING] computing qoi current index:",self.is_current_index_maximum_index)
            qoi_list = None
        # print("[SCREENING] qoi list:",qoi_list)
        return qoi_list

    def MappingAndEvaluateQuantityOfInterest(self):
        """
        function mapping the weighted pressure on reference model and calling evaluation of quantit of interest
        input:  self: an instance of the class
        """
        # map from current model part of interest to reference model part
        mapping_parameters = KratosMultiphysics.Parameters("""{
            "mapper_type": "nearest_element",
            "interface_submodel_part_origin": "FluidModelPart.NoSlip3D_structure",
            "interface_submodel_part_destination": "FluidModelPart.NoSlip3D_structure",
            "echo_level" : 3
            }""")
        mapper = KratosMultiphysics.MappingApplication.MapperFactory.CreateMapper(self._GetSolver().main_model_part,self.mapping_reference_model.GetModelPart("FluidModelPart"),mapping_parameters)
        mapper.Map(KratosMultiphysics.ExaquteSandboxApplication.PRESSURE_WEIGHTED, \
            KratosMultiphysics.ExaquteSandboxApplication.PRESSURE_WEIGHTED,        \
            KratosMultiphysics.MappingApplication.Mapper.FROM_NON_HISTORICAL |     \
            KratosMultiphysics.MappingApplication.Mapper.TO_NON_HISTORICAL)
        # evaluate qoi
        qoi_list = self.EvaluateQuantityOfInterest()
        return qoi_list